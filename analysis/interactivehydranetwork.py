import matplotlib.pyplot as plt
import yaml, json
import numpy as np
import os
from matplotlib.patches import FancyArrowPatch
from matplotlib.widgets import Button

# ============================================================
# === Load Geometry and JSON ===
# ============================================================

def load_hydra(geometry_yaml, network_json, io_group=1):
    with open(geometry_yaml) as fi:
        geo = yaml.safe_load(fi)
    chip_pix = {chip_id+1: pix for chip_id, pix in geo['chips']}
    chipid_pos = {}
    for chipid, pix in chip_pix.items():
        xs, ys = zip(*[(geo['pixels'][p][1], geo['pixels'][p][2]) for p in pix])
        chipid_pos[chipid] = dict(
            avgX=(max(xs)+min(xs))/2,
            avgY=(max(ys)+min(ys))/2,
            minX=min(xs), maxX=max(xs), minY=min(ys), maxY=max(ys)
        )

    with open(network_json, 'r') as f:
        data = json.load(f)

    hydra = data['network'][str(io_group)]
    chip_connections = {}
    ioc_chip = {}
    for ioc in hydra:
        ioc_chip[ioc] = []
        for node in hydra[ioc]["nodes"]:
            chip = int(node['chip_id']) if node['chip_id'] != 'ext' else None
            if chip is None or chip not in chipid_pos:
                continue
            ioc_chip[ioc].append(chip)
            for target in node['miso_us']:
                if target is not None:
                    chip_connections.setdefault(chip, []).append(target)

    return data, chipid_pos, chip_connections, ioc_chip


# ============================================================
# === Interactive Editor (for VSCode/terminal) ===
# ============================================================

colors = ['c', 'm', 'y', 'g']

def interactive_network_editor(geometry_yaml, network_json, io_group=1):
    data, chipid_pos, connections, ioc_chip = load_hydra(geometry_yaml, network_json, io_group)
    
    chips = sorted(chipid_pos.keys())
    outgoing = {cid: set(connections.get(cid, [])) for cid in chips}
    incoming = {cid: None for cid in chips}
    for src, targets in outgoing.items():
        for t in targets:
            incoming[t] = src

    fig, ax = plt.subplots(figsize=(10, 6))
    fig.canvas.manager.set_window_title("Hydra Network Editor")
    ax.set_aspect('equal')
    ax.set_title("Hydra Network Editor — drag to rewire, press 's' to save")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")

    xs = [v['avgX'] for v in chipid_pos.values()]
    ys = [v['avgY'] for v in chipid_pos.values()]
    ax.set_xlim(min(xs)-5, max(xs)+5)
    ax.set_ylim(min(ys)-5, max(ys)+5)

    drag_start = None
    preview_arrow = None

    def chip_to_ioc(chipID, ioc_chip):
        for ioc in ioc_chip.keys():
            if chipID in ioc_chip[str(ioc)]:
                return int(ioc)
        return 1  # default if not found

    def draw_arrows():
        ax.clear()
        ax.set_aspect('equal')
        ax.set_title("Hydra Network Editor — drag to rewire, press 's' to save")
        ax.set_xlabel('X Position [mm]')
        ax.set_ylabel('Y Position [mm]')
        ax.set_xlim(min(xs)-5, max(xs)+5)
        ax.set_ylim(min(ys)-5, max(ys)+5)

        # Draw arrows after labels
        for src in outgoing:
            ioc_idx = chip_to_ioc(src, ioc_chip) - 1
            color = colors[ioc_idx % len(colors)]
            for dst in outgoing[src]:
                if dst not in chipid_pos:
                    continue
                x0, y0 = chipid_pos[src]['avgX'], chipid_pos[src]['avgY']
                x1, y1 = chipid_pos[dst]['avgX'], chipid_pos[dst]['avgY']
                dx, dy = x1 - x0, y1 - y0
                ax.arrow(
                    x0, y0, dx * 0.8, dy * 0.8,
                    head_width=2, head_length=3,
                    fc=color, ec=color, alpha=0.8,
                    length_includes_head=True,
                    zorder=3  # draw on top of labels
                )

        # Chip circles
        for chip in chips:
            ax.annotate(
                str(chip),
                (chipid_pos[chip]['avgX'], chipid_pos[chip]['avgY']),
                ha='center', va='center', color='white',
                bbox=dict(boxstyle='circle, pad=0.3', fc='black', ec='white', lw=0.8, alpha=0.8),
                zorder=2
            )

        fig.canvas.draw_idle()

    def update_colors_after_rewire():
        """Propagate colors downstream so each subnetwork inherits the color of its root chip."""
        chip_colors = {}

        # Step 1: Base color assignment from I/O groups
        for i, ioc in enumerate(ioc_chip.keys()):
            base_color = colors[i % len(colors)]
            for chip in ioc_chip[ioc]:
                chip_colors[chip] = base_color

        # Step 2: Propagate colors downstream
        changed = True
        while changed:
            changed = False
            for src, dests in outgoing.items():
                if src not in chip_colors:
                    continue
                src_color = chip_colors[src]
                for dst in dests:
                    if chip_colors.get(dst) != src_color:
                        chip_colors[dst] = src_color
                        changed = True

        # Step 3: Redraw arrows
        for artist in list(ax.patches) + list(ax.lines):
            artist.remove()
        for src, dests in outgoing.items():
            color = chip_colors.get(src, "gray")
            if src not in chipid_pos:
                continue
            x0, y0 = chipid_pos[src]["avgX"], chipid_pos[src]["avgY"]
            for dst in dests:
                if dst not in chipid_pos:
                    continue
                x1, y1 = chipid_pos[dst]["avgX"], chipid_pos[dst]["avgY"]
                dx, dy = x1 - x0, y1 - y0
                ax.arrow(
                    x0, y0, dx * 0.8, dy * 0.8,
                    head_width=1.5, head_length=3,
                    fc=color, ec=color, alpha=0.6,
                    length_includes_head=True,
                    zorder=3  # draw on top of labels
                )
        fig.canvas.draw_idle()

    def get_chip_from_event(event):
        """Return the chip ID nearest to the mouse event."""
        if event.xdata is None or event.ydata is None:
            return None
        x, y = event.xdata, event.ydata
        tolerance = 8  # increase this to make it easier to click/drag
        for cid, pos in chipid_pos.items():
            dist = np.hypot(x - pos['avgX'], y - pos['avgY'])
            if dist <= tolerance:
                return cid
        return None

    def remove_incoming(target):
        src = incoming[target]
        if src is not None:
            outgoing[src].discard(target)
        incoming[target] = None

    def remove_reverse(a, b):
        if a in outgoing.get(b, set()):
            outgoing[b].discard(a)
            incoming[a] = None

    def on_press(event):
        nonlocal drag_start, preview_arrow
        drag_start = get_chip_from_event(event)
        if preview_arrow:
            preview_arrow.remove()
            preview_arrow = None

    def on_motion(event):
        nonlocal preview_arrow
        if drag_start is None or event.inaxes != ax:
            return
        if preview_arrow:
            preview_arrow.remove()
        sx, sy = chipid_pos[drag_start]['avgX'], chipid_pos[drag_start]['avgY']
        mx, my = event.xdata, event.ydata
        preview_arrow = ax.arrow(sx, sy, mx - sx, my - sy,
                                 head_width=0.4, head_length=0.6,
                                 fc='orange', ec='orange', alpha=0.5,
                                 length_includes_head=True)
        fig.canvas.draw_idle()

    def on_release(event):
        nonlocal drag_start, preview_arrow
        if drag_start is None:
            return
        if preview_arrow:
            preview_arrow.remove()
            preview_arrow = None
        chip_end = get_chip_from_event(event)
        if chip_end is not None and chip_end != drag_start:
            remove_reverse(drag_start, chip_end)
            remove_incoming(chip_end)
            outgoing[drag_start].add(chip_end)
            incoming[chip_end] = drag_start
        drag_start = None
        draw_arrows()
        update_colors_after_rewire()

    def export_to_json(default_name="edited_network.json"):
        """Prompt for a filename and save updated hydra network connections back to JSON."""
        import copy

        new_data = copy.deepcopy(data)  # safer deep copy
        io_key = str(io_group)

        if 'network' not in new_data or io_key not in new_data['network']:
            print("Could not find network structure in JSON")
            return

        # Ask user for filename
        filename = input(f"Enter output filename [{default_name}]: ").strip()
        if not filename:
            filename = default_name
        if not filename.endswith(".json"):
            filename += ".json"

        total_connections = 0
        for ioc in new_data['network'][io_key]:
            for node in new_data['network'][io_key][ioc]["nodes"]:
                cid_str = node.get('chip_id')
                if cid_str == 'ext':
                    continue
                try:
                    cid = int(cid_str)
                except (TypeError, ValueError):
                    continue

                if cid not in outgoing:
                    continue

                # Build a new list with all outgoing links (up to 4)
                targets = list(outgoing[cid])
                new_miso = [None, None, None, None]
                for i, t in enumerate(targets[:4]):
                    new_miso[i] = t
                    total_connections += 1
                node['miso_us'] = new_miso

        with open(filename, 'w') as f:
            json.dump(new_data, f, indent=2)

        print(f"Saved {total_connections} connections to '{filename}'")

    def on_key(event):
        if event.key == 's':  # press 's' to save
            export_to_json()
            print("Saved via keyboard shortcut")

    draw_arrows()
    fig.canvas.mpl_connect('button_press_event', on_press)
    fig.canvas.mpl_connect('button_release_event', on_release)
    fig.canvas.mpl_connect('motion_notify_event', on_motion)
    fig.canvas.mpl_connect('key_press_event', on_key)

    plt.show()


# Example usage:
#>>> from interactivehydranetwork import interactive_network_editor
#>>> interactive_network_editor('layout-3.0.0.yaml', 'iog_1-tile_3-hydra-network.json', io_group=1)