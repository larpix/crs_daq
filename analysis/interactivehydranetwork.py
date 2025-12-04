import matplotlib.pyplot as plt
import yaml, json
import numpy as np
from matplotlib.patches import Rectangle

colors = ['c', 'm', 'y', 'g']

def load_hydra(geometry_yaml, network_json, io_group=1):
    with open(geometry_yaml) as fi:
        geo = yaml.safe_load(fi)
    chip_pix = {chip_id+1: pix for chip_id, pix in geo['chips']}
    chipid_pos = {}
    for chipid, pix in chip_pix.items():
        xs, ys = zip(*[(geo['pixels'][p][1], geo['pixels'][p][2]) for p in pix])
        avgX = (max(xs)+min(xs))/2
        avgY = (max(ys)+min(ys))/2
        chipid_pos[chipid] = dict(
            avgX=avgX, avgY=avgY, minX=min(xs), maxX=max(xs), minY=min(ys), maxY=max(ys)
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
    return data, chipid_pos, chip_connections, ioc_chip, geo

def interactive_network_editor(geometry_yaml, network_json, io_group=1):
    data, chipid_pos, connections, ioc_chip, geo = load_hydra(geometry_yaml, network_json, io_group)

    chips = sorted(chipid_pos.keys())
    outgoing = {cid: set(connections.get(cid, [])) for cid in chips}
    incoming = {cid: None for cid in chips}
    for src, targets in outgoing.items():
        for t in targets:
            incoming[t] = src
    root_chips = {cid for cid in chips if incoming[cid] is None}

    drag_start = None
    preview_arrow = None
    undo_stack = []
    redo_stack = []

    # --- Neighbor function based on chip IDs ---
    def get_neighbors(chipID):
        return {chipID-1, chipID+1, chipID-10, chipID+10} & set(chips)

    # --- Chip to IOC ---
    def chip_to_ioc(chipID):
        for ioc in ioc_chip.keys():
            if chipID in ioc_chip[ioc]:
                return int(ioc)
        return 1

    # --- Compute reachable and root colors ---
    def compute_reachable_and_root_colors():
        reachable = set()
        chip_root_color = {}
        for root in root_chips:
            ioc_idx = chip_to_ioc(root)-1
            color = colors[ioc_idx % len(colors)]
            stack = [root]
            while stack:
                cur = stack.pop()
                if cur in reachable: continue
                reachable.add(cur)
                chip_root_color[cur] = color
                for nxt in outgoing.get(cur, []):
                    stack.append(nxt)
        return reachable, chip_root_color

    reachable, chip_root_color = compute_reachable_and_root_colors()

    # --- Draw function with rectangles and mm-grid ---
    def draw_network():
        ax.clear()
        ax.set_aspect('equal')
        ax.set_xlabel('X Position [mm]')
        ax.set_ylabel('Y Position [mm]')

        vertical_lines = np.linspace(-geo['width']/2, geo['width']/2, 17)
        horizontal_lines = np.linspace(-geo['height']/2, geo['height']/2, 11)
        ax.set_xticks(vertical_lines)
        ax.set_yticks(horizontal_lines)
        ax.set_xlim(vertical_lines[0]*1.1, vertical_lines[-1]*1.1)
        ax.set_ylim(horizontal_lines[0]*1.1, horizontal_lines[-1]*1.1)

        for vl in vertical_lines: ax.vlines(vl, horizontal_lines[0], horizontal_lines[-1], linestyle='dotted', color='k')
        for hl in horizontal_lines: ax.hlines(hl, vertical_lines[0], vertical_lines[-1], linestyle='dotted', color='k')
        
        # Draw rectangles for each chip
        for chip in chips:
            pos = chipid_pos[chip]
            color = 'white'
            edge = 'none'
            alpha = 0.5           
            if chip not in reachable: 
                color = 'red'
                alpha=0.5
                edge = 'k'
            if chip in root_chips: 
                color = 'blue'
                alpha=0.5
                edge = 'k'
            rect = Rectangle((pos['minX'], pos['minY']),
                            pos['maxX'] - pos['minX'],
                            pos['maxY'] - pos['minY'],
                            facecolor=color, edgecolor=edge, alpha=alpha)
            ax.add_patch(rect)
            ax.annotate(str(chip), (pos['avgX'], pos['avgY']),
                        ha='center', va='center', color='k')
            fig.canvas.draw_idle()

        # Draw arrows
        for src, dsts in outgoing.items():
            for dst in dsts:
                if dst not in chipid_pos:
                    continue
                x0, y0 = chipid_pos[src]['avgX'], chipid_pos[src]['avgY']
                x1, y1 = chipid_pos[dst]['avgX'], chipid_pos[dst]['avgY']
                dx, dy = x1 - x0, y1 - y0
                color = chip_root_color.get(src, 'gray') if src in reachable and dst in reachable else 'gray'
                ax.arrow(
                    x0, y0, dx*0.8, dy*0.8,
                    head_width=1.5, head_length=1.5,
                    fc=color, ec=color, alpha=0.8,
                    length_includes_head=True, zorder=3
                )
        fig.canvas.draw_idle()


    # --- Mouse events ---
    def get_chip_from_event(event):
        if event.xdata is None or event.ydata is None: return None
        x, y = event.xdata, event.ydata
        for cid, pos in chipid_pos.items():
            if np.hypot(x-pos['avgX'], y-pos['avgY']) <= 5:
                return cid
        return None

    def remove_incoming(target):
        src = incoming.get(target)
        if src is not None:
            outgoing[src].discard(target)
        incoming[target] = None

    def on_press(event):
        nonlocal drag_start, preview_arrow
        drag_start = get_chip_from_event(event)
        if preview_arrow: preview_arrow.remove(); preview_arrow=None

    def on_motion(event):
        nonlocal preview_arrow
        if drag_start is None or event.inaxes != ax: return
        if preview_arrow: preview_arrow.remove()
        sx, sy = chipid_pos[drag_start]['avgX'], chipid_pos[drag_start]['avgY']
        preview_arrow = ax.arrow(sx, sy, event.xdata-sx, event.ydata-sy, head_width=0.4, head_length=0.6, fc='orange', ec='orange', alpha=0.5)
        fig.canvas.draw_idle()

    def on_release(event):
        nonlocal drag_start, preview_arrow, reachable, chip_root_color
        if drag_start is None: return
        if preview_arrow: preview_arrow.remove(); preview_arrow=None
        chip_end = get_chip_from_event(event)
        if chip_end is not None and chip_end != drag_start:
            if chip_end not in get_neighbors(drag_start):
                print(f"Invalid connection: {drag_start} -> {chip_end}")
                drag_start = None
                return
            prev_parent = incoming.get(chip_end)
            remove_incoming(chip_end)
            outgoing[drag_start].add(chip_end)
            incoming[chip_end] = drag_start
            undo_stack.append(("add_arrow", drag_start, chip_end, prev_parent))
            redo_stack.clear()
        drag_start = None
        reachable, chip_root_color = compute_reachable_and_root_colors()
        draw_network()

    # --- Keyboard ---
    def export_to_json(default_name="edited_network.json"):
        import copy
        new_data = copy.deepcopy(data)
        io_key = str(io_group)
        filename = input(f"Enter output filename [{default_name}]: ").strip() or default_name
        if not filename.endswith('.json'): filename += '.json'
        for ioc in new_data['network'][io_key]:
            for node in new_data['network'][io_key][ioc]['nodes']:
                cid_str = node.get('chip_id')
                if cid_str=='ext': continue
                try: cid=int(cid_str)
                except: continue
                if cid not in outgoing: continue
                targets = list(outgoing[cid])
                new_miso = [None]*4
                for i, t in enumerate(targets[:4]):
                    new_miso[i] = t
                node['miso_us'] = new_miso
        with open(filename, 'w') as f: json.dump(new_data,f,indent=2)
        print(f"Saved {filename}")

    def on_key(event):
        key = event.key or ""
        if key=='s': export_to_json(); return
        if 'ctrl+z' in key or 'cmd+z' in key: on_undo(None); return
        if 'ctrl+Z' in key or 'cmd+Z' in key: on_redo(None); return

    def on_undo(event):
        nonlocal reachable, chip_root_color
        if not undo_stack: return
        action=undo_stack.pop(); redo_stack.append(action)
        if action[0]=='add_arrow':
            _,src,dst,prev=action
            outgoing[src].discard(dst)
            if prev is not None:
                outgoing.setdefault(prev,set()).add(dst)
                incoming[dst]=prev
            else: incoming[dst]=None
        reachable, chip_root_color = compute_reachable_and_root_colors()
        draw_network()

    def on_redo(event):
        nonlocal reachable, chip_root_color
        if not redo_stack: return
        action=redo_stack.pop(); undo_stack.append(action)
        if action[0]=='add_arrow':
            _,src,dst,prev=action
            if prev is not None: outgoing.setdefault(prev,set()).discard(dst)
            outgoing.setdefault(src,set()).add(dst)
            incoming[dst]=src
        reachable, chip_root_color = compute_reachable_and_root_colors()
        draw_network()

    # --- Connect ---
    fig, ax = plt.subplots(figsize=(16,10))
    fig.canvas.manager.set_window_title("Hydra Network Editor")
    draw_network()
    fig.canvas.mpl_connect('button_press_event', on_press)
    fig.canvas.mpl_connect('button_release_event', on_release)
    fig.canvas.mpl_connect('motion_notify_event', on_motion)
    fig.canvas.mpl_connect('key_press_event', on_key)
    plt.show()


# Example usage:
#>>> from interactivehydranetwork import interactive_network_editor
#>>> interactive_network_editor('layout-3.0.0.yaml', 'iog_1-tile_3-hydra-network.json', io_group=1)




'''edits:
add undo + redo
make diagonals not allowed, skipping over chips not allowed
 - should only work for the four neighboring chips
 - set distance limit
 - max 3 arrows pointing out
 - max 1 arrow pointing in
!does it automatically add the .json when you save the file name
!make sure it correctly saves the json file-> it works on this code but not the original hydra network plotting code
 - index 0=left
 - index 1=up
 - index 2=right
 - index 3=down
 the direction of the arrow should be based on the uart ^
if the user disconnected some chips from the root chips:
 - have it turn gray 
 !- all the arrows should be originating from the root chip -- no arrows can go into the root chips
 !- when exporting, send a message saying those chips are disconnected
Visuals: 
 !- replace the circle labels with dotted squares
 !- have it highlight when you hover over the circle to show it registered
 !- highlight the missing tiles like in the original plotting code
 - highlight the root chips like in original plotting code
 - make arrows fatter/more visible
 !- make sure the mm dist is accurate + set xlabels

'''

#test: pass created .json file through this script AND the original plotting script
# (.v3venv) jchakrani@labpix:~/larpix/FSD/v3/10x16/test_hydra_interface/crs_daq$ python analysis/plot_hydra_network_10x16.py --controller_config configs/iog_1-tile_1-hydra-network_test.json 