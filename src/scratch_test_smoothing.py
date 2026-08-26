import struct, math, os
from collections import defaultdict

def test_province_smoothing():
    bmp_path = "eu4_data/map/provinces.bmp"
    def_path = "eu4_data/map/definition.csv"

    rgb_to_pid = {}
    with open(def_path, "r", encoding="latin-1") as f:
        for line in f:
            parts = line.strip().split(";")
            if len(parts) >= 4:
                try:
                    pid = int(parts[0])
                    r = int(parts[1])
                    g = int(parts[2])
                    b = int(parts[3])
                    rgb_to_pid[(r << 16) | (g << 8) | b] = pid
                except ValueError:
                    pass

    with open(bmp_path, "rb") as f:
        f.seek(18)
        width, height = struct.unpack("<ii", f.read(8))
        f.seek(10)
        offset = struct.unpack("<I", f.read(4))[0]
        f.seek(offset)
        raw_data = f.read()

    row_size = ((width * 3 + 3) // 4) * 4

    # Let's test a slice in Europe / France / Germany
    min_x, max_x = 1500, 2100
    min_y, max_y = 500, 900
    out_w = max_x - min_x
    out_h = max_y - min_y

    grid = []
    for cy in range(min_y, max_y):
        iy = height - 1 - cy
        in_row_offset = iy * row_size
        row = []
        for cx in range(min_x, max_x):
            px_offset = in_row_offset + cx * 3
            b = raw_data[px_offset]
            g = raw_data[px_offset + 1]
            r = raw_data[px_offset + 2]
            key = (r << 16) | (g << 8) | b
            row.append(rgb_to_pid.get(key, 0))
        grid.append(row)

    prov_edges = defaultdict(dict)
    for y in range(out_h):
        for x in range(out_w):
            pid = grid[y][x]
            if pid == 0:
                continue

            top_pid = grid[y - 1][x] if y > 0 else 0
            if top_pid != pid:
                prov_edges[pid][(x, y)] = (x + 1, y)

            bot_pid = grid[y + 1][x] if y + 1 < out_h else 0
            if bot_pid != pid:
                prov_edges[pid][(x + 1, y + 1)] = (x, y + 1)

            left_pid = grid[y][x - 1] if x > 0 else 0
            if left_pid != pid:
                prov_edges[pid][(x, y + 1)] = (x, y)

            right_pid = grid[y][x + 1] if x + 1 < out_w else 0
            if right_pid != pid:
                prov_edges[pid][(x + 1, y)] = (x + 1, y + 1)

    def rdp_simplify(points, epsilon):
        if len(points) <= 2:
            return points
        def p_dist(p, a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            l = math.hypot(dx, dy)
            if l == 0: return math.hypot(p[0]-a[0], p[1]-a[1])
            return abs((p[0]-a[0])*dy - (p[1]-a[1])*dx) / l
        
        dmax, idx = 0.0, 0
        for i in range(1, len(points)-1):
            d = p_dist(points[i], points[0], points[-1])
            if d > dmax:
                dmax, idx = d, i
        if dmax > epsilon:
            r1 = rdp_simplify(points[:idx+1], epsilon)
            r2 = rdp_simplify(points[idx:], epsilon)
            return r1[:-1] + r2
        return [points[0], points[-1]]

    def simplify_loop(loop, eps=1.2):
        if len(loop) < 4:
            return loop
        half = len(loop) // 2
        p1 = rdp_simplify(loop[:half+1], eps)
        p2 = rdp_simplify(loop[half:] + [loop[0]], eps)
        res = p1[:-1] + p2[:-1]
        return res if len(res) >= 3 else loop

    def chaikin(pts, iters=1):
        curr = pts
        for _ in range(iters):
            smoothed = []
            n = len(curr)
            for i in range(n):
                p0, p1 = curr[i], curr[(i+1)%n]
                q = (0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1])
                r = (0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1])
                smoothed.extend([q, r])
            curr = smoothed
        return curr

    raw_paths = {}
    smooth_paths = {}

    for pid, edges in prov_edges.items():
        loops = []
        visited = set()
        for start_pt in edges:
            if start_pt in visited: continue
            curr = start_pt
            loop = [curr]
            visited.add(curr)
            while True:
                nxt = edges.get(curr)
                if not nxt or nxt == start_pt or nxt in visited: break
                loop.append(nxt)
                visited.add(nxt)
                curr = nxt
            if len(loop) >= 3:
                loops.append(loop)

        # Raw / collinear
        raw_d = []
        for l in loops:
            raw_d.append(f"M{l[0][0]} {l[0][1]}" + "".join(f"L{p[0]} {p[1]}" for p in l[1:]) + "Z")
        raw_paths[pid] = "".join(raw_d)

        # Smooth
        smooth_d = []
        for l in loops:
            s_l = simplify_loop(l, eps=1.0)
            ch_l = chaikin(s_l, iters=1)
            smooth_d.append(f"M{ch_l[0][0]:.1f} {ch_l[0][1]:.1f}" + "".join(f"L{p[0]:.1f} {p[1]:.1f}" for p in ch_l[1:]) + "Z")
        smooth_paths[pid] = "".join(smooth_d)

    print(f"Processed {len(prov_edges)} test provinces in Europe.")
    print("Sample raw path length:", len(list(raw_paths.values())[0]))
    print("Sample smooth path length:", len(list(smooth_paths.values())[0]))

test_province_smoothing()
