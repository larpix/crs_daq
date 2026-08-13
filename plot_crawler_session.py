#!/usr/bin/env python3
"""
plot_crawler_session.py

Presentation-oriented timeline plotter for the LArPix chip crawler.

Normal usage
------------
Point the script at one crawler session directory.  It discovers events.jsonl
and session.json, uses the crawler timestamps as the plot/query window, and
pulls packet rate plus PACMAN power monitoring directly from PacMon/InfluxDB.

    python plot_crawler_session.py \\
        crawler_logs/crawler_20260810_230038_CDT \\
        --pacman 6 \\
        --packet-tile 5

PacMon defaults
---------------
URL:      http://192.168.197.44:18086
Database: pacmon
User:     admin

Credentials are preferably supplied via the environment so they do not appear
in shell history:

    export PACMON_INFLUX_TOKEN='...'

or, for a username/password InfluxDB setup:

    export PACMON_INFLUX_PASSWORD='...'

The corresponding URL/database/user defaults can also be overridden with:
PACMON_INFLUX_URL, PACMON_INFLUX_DB, and PACMON_INFLUX_USER.

Packet rate is queried from data_statuses_rates, grouped in 10 s bins by
"tile_id".  PACMAN VDDD/VDDA/IDDD/IDDA are queried from pacman_power in 1 s
bins.  Power tile defaults to 1.

If more than one packet tile is returned, the script will not guess which one
belongs to the crawl.  Give --packet-tile TILE, or use --all-packet-tiles for a
diagnostic plot of all returned packet-rate series.

Legacy/offline CSV usage
------------------------
The old Grafana CSV workflow remains supported.  Supplying --packet-rate makes
the script use CSV mode and skips PacMon queries:

    python plot_crawler_session.py \\
        crawler_logs/crawler_20260810_230038_CDT \\
        --packet-rate packet_rate.csv \\
        --metric VDDD=vddd.csv \\
        --metric VDDA=vdda.csv \\
        --metric IDDD=iddd.csv \\
        --metric IDDA=idda.csv

The plot combines:
  1. measured packet rate,
  2. expected pedestal packet rate from the reconstructed active-chip count,
  3. PACMAN voltage/current monitoring,
  4. crawler event markers,
  5. shaded rebuild intervals.

The expected pedestal rate is:

    active_chips * larpix_clock_hz / periodic_trigger_cycles

using periodic_trigger_cycles from session.json when available.
"""

from __future__ import annotations

SCRIPT_VERSION = "2026-08-11-powerplot-v3"


import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

try:
    import requests
except ImportError as exc:  # pragma: no cover - environment dependent
    requests = None
    _REQUESTS_IMPORT_ERROR = exc
else:
    _REQUESTS_IMPORT_ERROR = None


DEFAULT_INFLUX_URL = "http://192.168.197.44:18086"
DEFAULT_INFLUX_DB = "pacmon"
DEFAULT_INFLUX_USER = "admin"
POWER_FIELDS = ("vddd", "vdda", "iddd", "idda")


# ---------------------------------------------------------------------------
# Session and data loading
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path}: invalid JSON: {exc}") from exc


def resolve_session_files(args) -> Tuple[Path, Optional[Path], Optional[Path], Optional[Path]]:
    """Resolve the crawler files while preserving the old --events workflow."""
    session_dir = args.session_dir

    if session_dir is not None:
        session_dir = session_dir.expanduser().resolve()
        if not session_dir.is_dir():
            raise RuntimeError(f"Crawler session directory does not exist: {session_dir}")

    if args.events is not None:
        events_path = args.events.expanduser().resolve()
    elif session_dir is not None:
        events_path = session_dir / "events.jsonl"
    else:
        raise RuntimeError("Give a crawler session directory or --events EVENTS.jsonl")

    if not events_path.exists():
        raise RuntimeError(f"Could not find crawler events file: {events_path}")

    if args.session is not None:
        session_path = args.session.expanduser().resolve()
    elif session_dir is not None:
        candidate = session_dir / "session.json"
        session_path = candidate if candidate.exists() else None
    else:
        session_path = None

    state_path = None
    crawler_log_path = None
    if session_dir is not None:
        candidate = session_dir / "state.json"
        state_path = candidate if candidate.exists() else None
        candidate = session_dir / "crawler.log"
        crawler_log_path = candidate if candidate.exists() else None

    return events_path, session_path, state_path, crawler_log_path


def load_events(path: Path) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"{path}: invalid JSON on line {line_no}: {exc}"
                ) from exc

    if not rows:
        raise RuntimeError(f"{path}: no events found")

    df = pd.DataFrame(rows)
    if "timestamp_local" not in df:
        raise RuntimeError(f"{path}: events do not contain timestamp_local")

    df["time"] = pd.to_datetime(df["timestamp_local"], errors="raise")
    sort_cols = ["time"] + (["seq"] if "seq" in df.columns else [])
    df = df.sort_values(sort_cols, kind="stable").reset_index(drop=True)
    return df


def infer_event_timezone(events: pd.DataFrame):
    first = events["time"].dropna().iloc[0]
    return first.tzinfo


def localize_or_convert(series: pd.Series, tzinfo) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    current_tz = getattr(parsed.dt, "tz", None)
    if current_tz is None:
        return parsed.dt.tz_localize(tzinfo)
    return parsed.dt.tz_convert(tzinfo)


def load_grafana_csv(path: Path, tzinfo, label: Optional[str] = None) -> pd.DataFrame:
    df = pd.read_csv(path)
    if df.shape[1] < 2:
        raise RuntimeError(f"{path}: expected at least two columns")

    time_col = df.columns[0]
    value_col = None
    best_numeric = -1
    for col in df.columns[1:]:
        numeric = pd.to_numeric(df[col], errors="coerce")
        count = int(numeric.notna().sum())
        if count > best_numeric:
            best_numeric = count
            value_col = col

    if value_col is None:
        raise RuntimeError(f"{path}: could not identify a value column")

    result = pd.DataFrame()
    result["time"] = localize_or_convert(df[time_col], tzinfo)
    result["value"] = pd.to_numeric(df[value_col], errors="coerce")
    result = result.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    result.attrs["source"] = str(path)
    result.attrs["source_column"] = str(value_col)
    result.attrs["label"] = label or str(value_col)
    return result


def parse_metric_arg(text: str) -> Tuple[str, Path]:
    if "=" not in text:
        path = Path(text)
        return path.stem, path
    label, filename = text.split("=", 1)
    label = label.strip()
    filename = filename.strip()
    if not label or not filename:
        raise argparse.ArgumentTypeError(
            "--metric must be LABEL=FILE.csv or simply FILE.csv"
        )
    return label, Path(filename)


def parse_bound(text: Optional[str], default: pd.Timestamp, tzinfo) -> pd.Timestamp:
    if text is None:
        return default
    ts = pd.Timestamp(text)
    if ts.tzinfo is None:
        return ts.tz_localize(tzinfo)
    return ts.tz_convert(tzinfo)


def infer_pacman_from_session(session: Dict[str, Any]) -> Optional[str]:
    value = session.get("run_config", {}).get("io_group")
    return None if value is None else str(value)


def infer_packet_tile_from_session(session: Dict[str, Any]) -> Optional[str]:
    """
    Use only explicit tile keys.  Do not guess a mapping from io_channel to tile_id.
    """
    run_config = session.get("run_config", {})
    for key in ("tile_id", "tile"):
        value = run_config.get(key)
        if value is not None:
            return str(value)
    return None


def _influx_quote(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _rfc3339_utc(ts: pd.Timestamp) -> str:
    if ts.tzinfo is None:
        raise ValueError("Influx query timestamp must be timezone-aware")
    utc = ts.tz_convert("UTC")
    # nanosecond precision is unnecessary here and some older servers are happier
    # with microseconds/seconds.
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _epoch_ms(ts: pd.Timestamp) -> int:
    """Return an aware pandas Timestamp as Unix epoch milliseconds."""
    if ts.tzinfo is None:
        raise ValueError("Influx query timestamp must be timezone-aware")
    return int(ts.tz_convert("UTC").value // 1_000_000)


def build_packet_rate_query(pacman: str, start: pd.Timestamp, end: pd.Timestamp,
                            packet_tile: Optional[str] = None) -> str:
    where = [f'"io_group"::tag = \'{_influx_quote(pacman)}\'']
    if packet_tile is not None:
        where.append(f'"tile_id"::tag = \'{_influx_quote(packet_tile)}\'')
    where.append(f"time >= '{_rfc3339_utc(start)}'")
    where.append(f"time <= '{_rfc3339_utc(end)}'")
    return (
        'SELECT mean("total") FROM "data_statuses_rates" WHERE ('
        + " AND ".join(where[:2] if packet_tile is not None else where[:1])
        + ") AND "
        + " AND ".join(where[2:] if packet_tile is not None else where[1:])
        + ' GROUP BY time(10s), "tile_id"::tag fill(null)'
    )


def build_power_query(field: str, pacman: str, power_tile: str,
                      start: pd.Timestamp, end: pd.Timestamp) -> str:
    """Build pacman_power InfluxQL in the same form used by Grafana."""
    if field not in POWER_FIELDS:
        raise ValueError(f"Unsupported PACMAN power field: {field}")

    pacman_regex = re.escape(str(pacman)).replace("/", r"\/")
    return (
        f'SELECT mean("{field}") FROM "pacman_power" '
        f'WHERE ("io_group"::tag =~ /^{pacman_regex}$/ '
        f"AND \"tile\"::tag = '{_influx_quote(power_tile)}') "
        f"AND time >= {_epoch_ms(start)}ms "
        f"AND time <= {_epoch_ms(end)}ms "
        'GROUP BY time(1s), "io_group"::tag, "tile"::tag fill(null) '
        'ORDER BY time ASC'
    )


class InfluxQueryError(RuntimeError):
    pass


class PacmonInfluxClient:
    def __init__(self, url: str, database: str, user: Optional[str],
                 password: Optional[str], token: Optional[str], timeout: float = 15.0,
                 verbose: bool = False):
        if requests is None:
            raise RuntimeError(
                "PacMon mode requires the 'requests' package. Install it with "
                "'python -m pip install requests'."
            ) from _REQUESTS_IMPORT_ERROR
        self.url = url.rstrip("/")
        self.database = database
        self.user = user
        self.password = password
        self.token = token
        self.timeout = timeout
        self.verbose = verbose

    @property
    def endpoint(self) -> str:
        return self.url + "/query"

    def _request(self, query: str, *, token_header: bool = True,
                 token_as_password: bool = False):
        params = {"db": self.database, "q": query, "epoch": "ms"}
        headers = {"Accept": "application/json"}

        if self.password is not None and self.user:
            params["u"] = self.user
            params["p"] = self.password
        elif token_as_password and self.token is not None and self.user:
            params["u"] = self.user
            params["p"] = self.token
        elif token_header and self.token is not None:
            headers["Authorization"] = f"Token {self.token}"

        if self.verbose:
            print(f"Influx query: {query}")

        return requests.get(
            self.endpoint,
            params=params,
            headers=headers,
            timeout=self.timeout,
        )

    def query(self, query: str) -> Dict[str, Any]:
        response = self._request(query)

        # Some v1-compatible deployments accept an API token as the v1
        # password rather than via Authorization: Token.  Retry that form only
        # after an authentication failure so a normal token-header setup stays
        # the default.
        if response.status_code in (401, 403) and self.token and self.user and not self.password:
            response = self._request(query, token_header=False, token_as_password=True)

        try:
            payload = response.json()
        except ValueError:
            payload = None

        if not response.ok:
            detail = ""
            if isinstance(payload, dict):
                detail = payload.get("error") or ""
            if not detail:
                detail = response.text.strip()[:500]
            raise InfluxQueryError(
                f"InfluxDB request failed ({response.status_code}) at {self.endpoint}: {detail}"
            )

        if not isinstance(payload, dict):
            raise InfluxQueryError("InfluxDB returned a non-JSON response")

        for result in payload.get("results", []):
            if "error" in result:
                raise InfluxQueryError(f"InfluxDB query error: {result['error']}")
        return payload


def influx_payload_to_series(payload: Dict[str, Any], tzinfo) -> List[Tuple[Dict[str, str], pd.DataFrame]]:
    """Convert InfluxDB v1 JSON series into [(tags, time/value dataframe), ...]."""
    output: List[Tuple[Dict[str, str], pd.DataFrame]] = []
    for result in payload.get("results", []):
        for series in result.get("series", []) or []:
            columns = series.get("columns", [])
            values = series.get("values", []) or []
            tags = {str(k): str(v) for k, v in (series.get("tags") or {}).items()}
            if not columns or "time" not in columns:
                continue

            frame = pd.DataFrame(values, columns=columns)
            value_cols = [c for c in columns if c != "time"]
            if not value_cols:
                continue
            value_col = value_cols[0]

            numeric_time = pd.to_numeric(frame["time"], errors="coerce")
            if numeric_time.notna().all():
                time = pd.to_datetime(numeric_time, unit="ms", utc=True, errors="coerce")
            else:
                time = pd.to_datetime(frame["time"], utc=True, errors="coerce")

            out = pd.DataFrame({
                "time": time.dt.tz_convert(tzinfo),
                "value": pd.to_numeric(frame[value_col], errors="coerce"),
            })
            out = out.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
            output.append((tags, out))
    return output


def natural_tag_key(value: str):
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


def load_packet_rate_from_influx(client: PacmonInfluxClient, pacman: str,
                                 start: pd.Timestamp, end: pd.Timestamp,
                                 tzinfo, packet_tile: Optional[str],
                                 all_packet_tiles: bool
                                 ) -> Tuple[List[Tuple[str, pd.DataFrame]], List[str]]:
    payload = client.query(build_packet_rate_query(pacman, start, end, packet_tile))
    raw = influx_payload_to_series(payload, tzinfo)
    by_tile: Dict[str, pd.DataFrame] = {}
    for tags, frame in raw:
        tile = tags.get("tile_id", "unknown")
        by_tile[tile] = frame

    available = sorted(by_tile, key=natural_tag_key)
    if not available:
        raise RuntimeError(
            f"PacMon returned no packet-rate data for PACMAN/io_group {pacman} "
            f"between {start} and {end}."
        )

    if packet_tile is not None:
        selected = str(packet_tile)
        if selected not in by_tile:
            raise RuntimeError(
                f"No packet-rate series for tile_id={selected}. "
                f"Available tile_id values: {', '.join(available)}"
            )
        return [(f"Packet rate — tile {selected}", by_tile[selected])], available

    if all_packet_tiles:
        return [(f"Packet rate — tile {tile}", by_tile[tile]) for tile in available], available

    if len(available) == 1:
        tile = available[0]
        return [(f"Packet rate — tile {tile}", by_tile[tile])], available

    raise RuntimeError(
        "PacMon returned multiple packet-rate tile_id series and the crawler session "
        "does not identify one unambiguously. Use --packet-tile TILE. "
        f"Available tile_id values: {', '.join(available)}. "
        "For diagnostics, --all-packet-tiles plots them all."
    )


def load_power_metrics_from_influx(client: PacmonInfluxClient, pacman: str,
                                   power_tile: str, start: pd.Timestamp,
                                   end: pd.Timestamp, tzinfo
                                   ) -> List[Tuple[str, pd.DataFrame]]:
    metrics: List[Tuple[str, pd.DataFrame]] = []
    if client.verbose:
        print("Power query mode: Grafana-compatible regex + epoch-ms + ORDER BY ASC")
    for field in POWER_FIELDS:
        payload = client.query(build_power_query(field, pacman, power_tile, start, end))
        series = influx_payload_to_series(payload, tzinfo)
        if not series:
            print(f"Warning: no {field.upper()} data returned for power tile {power_tile}")
            continue
        # The query fixes io_group and tile, so normally there is exactly one series.
        for idx, (tags, frame) in enumerate(series):
            non_null = int(frame["value"].notna().sum())
            if client.verbose:
                print(
                    f"Influx result: {field.upper()} power tile {power_tile}: "
                    f"{len(frame)} buckets, {non_null} non-null samples, tags={tags}"
                )
            if non_null == 0:
                print(
                    f"Warning: {field.upper()} returned a series for power tile "
                    f"{power_tile}, but every value is null."
                )
            label = field.upper() if len(series) == 1 else f"{field.upper()} #{idx + 1}"
            metrics.append((label, frame))
    return metrics


# ---------------------------------------------------------------------------
# Event interpretation
# ---------------------------------------------------------------------------

def edge_from_row(row: pd.Series) -> Optional[Dict[str, Any]]:
    edge = row.get("edge")
    return edge if isinstance(edge, dict) else None


@dataclass
class RebuildSpan:
    start: pd.Timestamp
    end: pd.Timestamp
    target_count: int
    pending_chip: Optional[int]


def rebuild_spans(events: pd.DataFrame) -> List[RebuildSpan]:
    spans: List[RebuildSpan] = []
    open_row: Optional[pd.Series] = None

    for _, row in events.iterrows():
        if row["event"] == "REBUILD_BEGIN":
            open_row = row
        elif row["event"] == "REBUILD_END" and open_row is not None:
            accepted = open_row.get("accepted_edges")
            accepted_count = len(accepted) if isinstance(accepted, list) else 0
            pending = open_row.get("pending")
            pending_chip = (
                pending.get("daughter")
                if isinstance(pending, dict)
                else None
            )
            target_count = 1 + accepted_count + (1 if pending_chip is not None else 0)
            spans.append(
                RebuildSpan(
                    start=open_row["time"],
                    end=row["time"],
                    target_count=target_count,
                    pending_chip=pending_chip,
                )
            )
            open_row = None

    return spans


def active_chip_steps(events: pd.DataFrame) -> pd.DataFrame:
    """
    Reconstruct steady-state active-chip count.

    A completed REBUILD_END establishes the topology described by its matching
    REBUILD_BEGIN.  A reset-only USER_SAFE_RESET drops the steady-state count
    to zero at RESET_END.
    """
    spans = rebuild_spans(events)
    rows: List[Dict[str, Any]] = []

    for span in spans:
        rows.append({"time": span.end, "active_chips": span.target_count})

    # Detect reset-only end after USER_SAFE_RESET.
    safe_reset_time = None
    for _, row in events.iterrows():
        if row["event"] == "USER_SAFE_RESET":
            safe_reset_time = row["time"]
        elif (
            row["event"] == "RESET_END"
            and safe_reset_time is not None
            and row["time"] >= safe_reset_time
        ):
            rows.append({"time": row["time"], "active_chips": 0})
            safe_reset_time = None

    if not rows:
        return pd.DataFrame(columns=["time", "active_chips"])

    out = pd.DataFrame(rows).sort_values("time", kind="stable")
    # If two updates share nearly the same timestamp, later one wins.
    out = out.drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)
    return out


def pair_expansions_with_first_trial_live(events: pd.DataFrame) -> pd.DataFrame:
    """
    Pair each USER_EXPAND with the first matching trial CHIP_LIVE event that
    occurs before the next USER_EXPAND/USER_BACK acceptance decision.

    This gives the hardware timestamp at which a newly requested candidate
    actually becomes live, rather than the earlier user-command timestamp.
    """
    event_rows = events.reset_index(drop=True)
    result: List[Dict[str, Any]] = []

    for idx, row in event_rows[event_rows["event"] == "USER_EXPAND"].iterrows():
        edge = edge_from_row(row)
        if not edge:
            continue
        daughter = edge.get("daughter")

        live_row = None
        for j in range(idx + 1, len(event_rows)):
            r = event_rows.iloc[j]
            if r["event"] == "USER_EXPAND":
                break
            if (
                r["event"] == "CHIP_LIVE"
                and bool(r.get("is_trial", False))
                and r.get("chip") == daughter
            ):
                live_row = r
                break

        result.append(
            {
                "requested_time": row["time"],
                "live_time": live_row["time"] if live_row is not None else pd.NaT,
                "mother": edge.get("mother"),
                "daughter": daughter,
                "direction": edge.get("direction"),
            }
        )

    return pd.DataFrame(result)


def make_event_summary(events: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []

    trial_lives = pair_expansions_with_first_trial_live(events)
    for _, r in trial_lives.iterrows():
        if pd.isna(r["live_time"]):
            continue
        rows.append(
            {
                "time": r["live_time"],
                "category": "candidate_live",
                "label": f"+{int(r['daughter'])}",
                "detail": (
                    f"{int(r['mother'])} -> {int(r['daughter'])} "
                    f"{str(r['direction']).upper()}"
                ),
            }
        )

    for _, row in events.iterrows():
        event = row["event"]
        if event == "USER_RECONFIGURE":
            rows.append(
                {
                    "time": row["time"],
                    "category": "reconfigure",
                    "label": "reconfig",
                    "detail": "Operator requested reset/reconfigure",
                }
            )
        elif event == "USER_ACCEPT":
            edge = edge_from_row(row)
            chip = edge.get("daughter") if edge else None
            rows.append(
                {
                    "time": row["time"],
                    "category": "accept",
                    "label": f"accept {chip}" if chip is not None else "accept",
                    "detail": row.get("message", ""),
                }
            )
        elif event == "USER_BACK":
            edge = edge_from_row(row)
            chip = edge.get("daughter") if edge else None
            rows.append(
                {
                    "time": row["time"],
                    "category": "back",
                    "label": f"back {chip}" if chip is not None else "back",
                    "detail": row.get("message", ""),
                }
            )
        elif event == "OPERATOR_NOTE":
            rows.append(
                {
                    "time": row["time"],
                    "category": "note",
                    "label": "",
                    "detail": row.get("message", ""),
                }
            )
        elif event == "USER_SAFE_RESET":
            rows.append(
                {
                    "time": row["time"],
                    "category": "safe_reset",
                    "label": "safe reset",
                    "detail": row.get("message", ""),
                }
            )

    if not rows:
        return pd.DataFrame(columns=["time", "category", "label", "detail"])

    out = pd.DataFrame(rows).sort_values("time", kind="stable").reset_index(drop=True)

    # Assign compact note numbers after chronological sorting.
    note_number = 0
    labels = []
    for _, row in out.iterrows():
        if row["category"] == "note":
            note_number += 1
            labels.append(f"N{note_number}")
        else:
            labels.append(row["label"])
    out["label"] = labels
    return out


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------

def expected_packet_rate(
    steps: pd.DataFrame,
    periodic_trigger_cycles: Optional[float],
    clock_hz: float,
) -> pd.DataFrame:
    if steps.empty or not periodic_trigger_cycles or periodic_trigger_cycles <= 0:
        return pd.DataFrame(columns=["time", "expected_rate"])
    out = steps.copy()
    per_chip = clock_hz / float(periodic_trigger_cycles)
    out["expected_rate"] = out["active_chips"] * per_chip
    return out[["time", "expected_rate"]]


def clip_df(df: pd.DataFrame, start, end) -> pd.DataFrame:
    if df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    if start is not None:
        mask &= df["time"] >= start
    if end is not None:
        mask &= df["time"] <= end
    return df.loc[mask].copy()


def draw_rebuild_spans(axes, spans: List[RebuildSpan], start=None, end=None):
    for span in spans:
        if start is not None and span.end < start:
            continue
        if end is not None and span.start > end:
            continue
        for ax in axes:
            ax.axvspan(span.start, span.end, alpha=0.07, linewidth=0)


def plot_event_strip(ax, summary: pd.DataFrame, start=None, end=None):
    categories = {
        "candidate_live": 4,
        "reconfigure": 3,
        "accept": 2,
        "back": 2,
        "safe_reset": 1,
        "note": 0,
    }
    ylabels = {
        4: "candidate live",
        3: "reconfigure",
        2: "accept / back",
        1: "safe reset",
        0: "notes",
    }

    summary = clip_df(summary, start, end)

    for category, group in summary.groupby("category", sort=False):
        y = categories[category]
        ax.scatter(group["time"], [y] * len(group), s=22, label=category)
        for _, row in group.iterrows():
            if category == "candidate_live":
                ax.annotate(
                    row["label"],
                    (row["time"], y),
                    xytext=(0, 7),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    rotation=90,
                    fontsize=8,
                )
            elif category in {"back", "safe_reset"}:
                ax.annotate(
                    row["label"],
                    (row["time"], y),
                    xytext=(4, 5),
                    textcoords="offset points",
                    ha="left",
                    va="bottom",
                    fontsize=8,
                )
            elif category == "note":
                ax.annotate(
                    row["label"],
                    (row["time"], y),
                    xytext=(0, 6),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )

    ax.set_yticks(sorted(ylabels))
    ax.set_yticklabels([ylabels[y] for y in sorted(ylabels)])
    ax.set_ylim(-0.6, 4.8)
    ax.grid(axis="x", alpha=0.2)
    ax.grid(axis="y", alpha=0.1)


def group_metrics(metrics: List[Tuple[str, pd.DataFrame]]):
    voltage = []
    current = []
    other = []
    for label, df in metrics:
        upper = label.upper()
        if upper.startswith("V"):
            voltage.append((label, df))
        elif upper.startswith("I"):
            current.append((label, df))
        else:
            other.append((label, df))
    groups = []
    if voltage:
        groups.append(("Supply voltage", voltage))
    if current:
        groups.append(("Supply current", current))
    for item in other:
        groups.append((item[0], [item]))
    return groups


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Plot a LArPix crawler session and, by default, query packet rate and "
            "PACMAN power monitoring directly from PacMon/InfluxDB."
        )
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {SCRIPT_VERSION}")
    p.add_argument(
        "session_dir",
        type=Path,
        nargs="?",
        help="Crawler session directory containing events.jsonl/session.json/state.json/crawler.log.",
    )
    p.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Override events.jsonl path (legacy mode or unusual directory layout).",
    )
    p.add_argument(
        "--session",
        type=Path,
        default=None,
        help="Override session.json path.",
    )

    source = p.add_argument_group("PacMon / InfluxDB")
    source.add_argument(
        "--pacman",
        default=None,
        help="PACMAN io_group. Default: infer run_config.io_group from session.json.",
    )
    source.add_argument(
        "--packet-tile",
        default=None,
        help="Select packet-rate tile_id. Required if multiple tile_id series are returned and session.json does not specify one.",
    )
    source.add_argument(
        "--all-packet-tiles",
        action="store_true",
        help="Plot every packet-rate tile_id returned by PacMon (diagnostic mode).",
    )
    source.add_argument(
        "--power-tile",
        default="1",
        help="pacman_power tile tag to query (default: 1).",
    )
    source.add_argument(
        "--no-power",
        action="store_true",
        help="Skip VDDD/VDDA/IDDD/IDDA queries.",
    )
    source.add_argument(
        "--influx-url",
        default=os.getenv("PACMON_INFLUX_URL", DEFAULT_INFLUX_URL),
        help=f"InfluxDB base URL (default: $PACMON_INFLUX_URL or {DEFAULT_INFLUX_URL}).",
    )
    source.add_argument(
        "--influx-db",
        default=os.getenv("PACMON_INFLUX_DB", DEFAULT_INFLUX_DB),
        help=f"InfluxDB database (default: $PACMON_INFLUX_DB or {DEFAULT_INFLUX_DB}).",
    )
    source.add_argument(
        "--influx-user",
        default=os.getenv("PACMON_INFLUX_USER", DEFAULT_INFLUX_USER),
        help=f"InfluxDB user (default: $PACMON_INFLUX_USER or {DEFAULT_INFLUX_USER}).",
    )
    source.add_argument(
        "--influx-password",
        default=os.getenv("PACMON_INFLUX_PASSWORD"),
        help="InfluxDB password. Prefer PACMON_INFLUX_PASSWORD instead of CLI history.",
    )
    source.add_argument(
        "--influx-token",
        default=os.getenv("PACMON_INFLUX_TOKEN"),
        help="InfluxDB API token. Prefer PACMON_INFLUX_TOKEN instead of CLI history.",
    )
    source.add_argument(
        "--influx-timeout",
        type=float,
        default=15.0,
        help="InfluxDB HTTP timeout in seconds (default: 15).",
    )
    source.add_argument(
        "--query-padding",
        type=float,
        default=30.0,
        help="Seconds queried before/after visible plot window (default: 30).",
    )
    source.add_argument(
        "--print-queries",
        action="store_true",
        help="Print generated InfluxQL queries (credentials are never printed).",
    )

    legacy = p.add_argument_group("Legacy/offline Grafana CSV mode")
    legacy.add_argument(
        "--packet-rate",
        "--packet-rate-csv",
        dest="packet_rate_csv",
        type=Path,
        default=None,
        help="Packet-rate Grafana CSV. Supplying this switches to CSV mode and skips PacMon queries.",
    )
    legacy.add_argument(
        "--metric",
        action="append",
        default=[],
        metavar="LABEL=FILE.csv",
        help="Additional Grafana CSV; repeat for VDDD/VDDA/IDDD/IDDA.",
    )

    plot = p.add_argument_group("Plot options")
    plot.add_argument(
        "--timezone",
        default=None,
        help="Display timezone, e.g. America/Chicago. Default: crawler event UTC offset.",
    )
    plot.add_argument(
        "--clock-hz",
        type=float,
        default=10_000_000.0,
        help="LArPix system clock for expected pedestal rate (default: 10 MHz).",
    )
    plot.add_argument(
        "--periodic-trigger-cycles",
        type=float,
        default=None,
        help="Override periodic_trigger_cycles from session.json.",
    )
    plot.add_argument("--start", default=None, help="Optional visible plot start time.")
    plot.add_argument("--end", default=None, help="Optional visible plot end time.")
    plot.add_argument("--title", default=None)
    plot.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Plot output. Default: SESSION_DIR/crawler_summary.png, or ./crawler_summary.png.",
    )
    plot.add_argument(
        "--event-summary",
        type=Path,
        default=None,
        help="Event-summary CSV output. Default: alongside plot.",
    )
    plot.add_argument("--dpi", type=int, default=180)
    plot.add_argument(
        "--no-expected-rate",
        action="store_true",
        help="Do not overlay expected packet rate.",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"Crawler plotter version: {SCRIPT_VERSION}")

    if args.all_packet_tiles and args.packet_tile is not None:
        raise RuntimeError("Use either --packet-tile or --all-packet-tiles, not both.")
    if args.query_padding < 0:
        raise RuntimeError("--query-padding must be >= 0")

    events_path, session_path, state_path, crawler_log_path = resolve_session_files(args)
    session = load_json(session_path) if session_path is not None else {}
    events = load_events(events_path)

    if args.timezone:
        tzinfo = args.timezone
        events["time"] = events["time"].dt.tz_convert(args.timezone)
    else:
        tzinfo = infer_event_timezone(events)

    event_start = events["time"].min()
    event_end = events["time"].max()
    plot_start = parse_bound(args.start, event_start, tzinfo)
    plot_end = parse_bound(args.end, event_end, tzinfo)
    if plot_end <= plot_start:
        raise RuntimeError(f"Plot end must be after plot start: {plot_start} -> {plot_end}")

    padding = pd.Timedelta(seconds=args.query_padding)
    query_start = plot_start - padding
    query_end = plot_end + padding

    inferred_pacman = infer_pacman_from_session(session)
    pacman = str(args.pacman) if args.pacman is not None else inferred_pacman

    inferred_packet_tile = infer_packet_tile_from_session(session)
    packet_tile = str(args.packet_tile) if args.packet_tile is not None else inferred_packet_tile

    metrics: List[Tuple[str, pd.DataFrame]] = []
    packet_series: List[Tuple[str, pd.DataFrame]] = []
    available_packet_tiles: List[str] = []
    data_source = "PacMon"

    if args.packet_rate_csv is not None:
        data_source = "Grafana CSV"
        packet_series = [(
            "Measured packet rate",
            load_grafana_csv(args.packet_rate_csv, tzinfo, label="Packet rate"),
        )]
        for item in args.metric:
            label, path = parse_metric_arg(item)
            metrics.append((label, load_grafana_csv(path, tzinfo, label=label)))
    else:
        if pacman is None:
            raise RuntimeError(
                "PacMon mode needs --pacman PACMAN, unless session.json contains run_config.io_group."
            )

        client = PacmonInfluxClient(
            url=args.influx_url,
            database=args.influx_db,
            user=args.influx_user,
            password=args.influx_password,
            token=args.influx_token,
            timeout=args.influx_timeout,
            verbose=args.print_queries,
        )

        packet_series, available_packet_tiles = load_packet_rate_from_influx(
            client,
            pacman=pacman,
            start=query_start,
            end=query_end,
            tzinfo=tzinfo,
            packet_tile=packet_tile,
            all_packet_tiles=args.all_packet_tiles,
        )
        if not args.no_power:
            metrics = load_power_metrics_from_influx(
                client,
                pacman=pacman,
                power_tile=str(args.power_tile),
                start=query_start,
                end=query_end,
                tzinfo=tzinfo,
            )

    periodic_trigger_cycles = args.periodic_trigger_cycles
    if periodic_trigger_cycles is None:
        try:
            periodic_trigger_cycles = float(
                session["pedestal_values"]["periodic_trigger_cycles"]
            )
        except (KeyError, TypeError, ValueError):
            periodic_trigger_cycles = None

    spans = rebuild_spans(events)
    steps = active_chip_steps(events)
    summary = make_event_summary(events)
    expected = expected_packet_rate(steps, periodic_trigger_cycles, args.clock_hz)

    metric_groups = group_metrics(metrics)
    nrows = 2 + len(metric_groups)
    height_ratios = [4] + [2.2] * len(metric_groups) + [1.8]

    fig, axes = plt.subplots(
        nrows=nrows,
        ncols=1,
        sharex=True,
        figsize=(15, 3.0 + 2.2 * len(metric_groups) + 3.2),
        gridspec_kw={"height_ratios": height_ratios},
        constrained_layout=True,
    )
    axes = list(axes) if hasattr(axes, "__iter__") else [axes]
    data_axes = axes[:-1]
    event_ax = axes[-1]

    rate_ax = data_axes[0]
    for label, rate_df in packet_series:
        r = clip_df(rate_df, plot_start, plot_end)
        rate_ax.plot(
            r["time"],
            r["value"],
            marker=".",
            markersize=3,
            linewidth=1.2,
            label=label,
        )

    show_expected = not args.no_expected_rate and not expected.empty
    if args.all_packet_tiles and len(packet_series) > 1:
        # Expected rate belongs to one crawler network, not the aggregate set of
        # packet tile series.  Avoid implying a correspondence we do not know.
        show_expected = False

    if show_expected:
        previous = expected[expected["time"] <= plot_start].tail(1)
        visible = expected[(expected["time"] >= plot_start) & (expected["time"] <= plot_end)]
        exp_visible = pd.concat([previous, visible]).drop_duplicates("time")
        if not exp_visible.empty:
            rate_ax.step(
                exp_visible["time"],
                exp_visible["expected_rate"],
                where="post",
                linewidth=1.3,
                linestyle="--",
                label="Expected from active-chip count",
            )

    rate_ax.set_ylabel("Packet rate [s$^{-1}$]")
    rate_ax.grid(alpha=0.22)
    rate_ax.legend(loc="upper left")
    rate_ax.set_yscale("symlog", linthresh=1)

    for ax, (group_title, group) in zip(data_axes[1:], metric_groups):
        for label, df in group:
            # Influx queries use fill(null), and PACMAN power is typically
            # sampled much more slowly than the 1 s GROUP BY cadence.  Keeping
            # those null buckets in the matplotlib series causes every real
            # sample to be isolated by NaNs, so a line-only plot appears empty.
            # Plot only actual measurements and mark each sample explicitly.
            d = clip_df(df, plot_start, plot_end).dropna(subset=["value"])
            if d.empty:
                continue
            ax.plot(
                d["time"],
                d["value"],
                linewidth=1.0,
                marker=".",
                markersize=3.5,
                label=label,
            )
        ax.set_ylabel(group_title)
        ax.grid(alpha=0.22)
        ax.legend(loc="upper left")

    draw_rebuild_spans(data_axes, spans, plot_start, plot_end)
    plot_event_strip(event_ax, summary, plot_start, plot_end)
    event_ax.set_xlabel("Local time")

    locator = mdates.AutoDateLocator(minticks=5, maxticks=12, tz=tzinfo)
    formatter = mdates.ConciseDateFormatter(locator, tz=tzinfo)
    event_ax.xaxis.set_major_locator(locator)
    event_ax.xaxis.set_major_formatter(formatter)

    for ax in axes:
        ax.set_xlim(plot_start, plot_end)

    if args.title:
        title = args.title
    else:
        run_config = session.get("run_config", {})
        iog = run_config.get("io_group", pacman)
        ioch = run_config.get("io_channel")
        root = run_config.get("root_chip")
        parts = ["LArPix crawler"]
        if iog is not None:
            parts.append(f"IOG {iog}")
        if packet_tile is not None and not args.all_packet_tiles:
            parts.append(f"tile {packet_tile}")
        if ioch is not None:
            parts.append(f"io_channel {ioch}")
        if root is not None:
            parts.append(f"root {root}")
        title = " — ".join(parts)
    fig.suptitle(title)

    if args.output is not None:
        output = args.output
    elif args.session_dir is not None:
        output = args.session_dir / "crawler_summary.png"
    else:
        output = Path("crawler_summary.png")
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=args.dpi)
    plt.close(fig)

    summary_path = args.event_summary
    if summary_path is None:
        summary_path = output.with_name(output.stem + "_events.csv")
    summary_path = summary_path.expanduser()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_out = summary.copy()
    if not summary_out.empty:
        summary_out["time"] = summary_out["time"].astype(str)
    summary_out.to_csv(summary_path, index=False)

    print(f"Crawler events: {events_path}")
    if session_path is not None:
        print(f"Session config: {session_path}")
    if state_path is not None:
        print(f"State file: {state_path}")
    if crawler_log_path is not None:
        print(f"Crawler log: {crawler_log_path}")
    print(f"Data source: {data_source}")
    if data_source == "PacMon":
        print(f"PacMon: {args.influx_url}  database={args.influx_db}  pacman/io_group={pacman}")
        print(f"Visible window: {plot_start} -> {plot_end}")
        print(f"Queried window: {query_start} -> {query_end}")
        if available_packet_tiles:
            print(f"Packet tile_id values returned: {', '.join(available_packet_tiles)}")
        if packet_tile is not None:
            print(f"Selected packet tile_id: {packet_tile}")
        elif args.all_packet_tiles:
            print("Selected packet tile_id: all")
        print(f"Power tile: {args.power_tile}{' (not queried)' if args.no_power else ''}")
    print(f"Wrote plot: {output}")
    print(f"Wrote event summary: {summary_path}")
    print(f"Rebuilds: {len(spans)}")
    print(
        "Candidate expansions with live timestamps: "
        f"{len(pair_expansions_with_first_trial_live(events))}"
    )
    if periodic_trigger_cycles:
        per_chip = args.clock_hz / periodic_trigger_cycles
        print(f"Expected pedestal rate per active chip: {per_chip:.4f} packets/s")
    else:
        print("Expected pedestal rate: unavailable (periodic_trigger_cycles not found)")
    if not steps.empty:
        print(f"Maximum reconstructed active-chip count: {int(steps['active_chips'].max())}")

    notes = summary[summary["category"] == "note"]
    if not notes.empty:
        print("\nOperator notes:")
        for _, row in notes.iterrows():
            print(f"  {row['label']}  {row['time']}  {row['detail']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
