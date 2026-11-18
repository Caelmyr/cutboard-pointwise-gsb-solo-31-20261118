"""自动直切排版引擎。

目标：把若干裁片按当前纸面尺寸与间隔重新排版，每页必须是“直切板”
（guillotine-cuttable）——可以依次用贯穿当前剩余矩形的横切或竖切把全部裁片分开，
切线落在间隔通道内，不穿过任何裁片。

规模限制：1–8 个裁片、纸面宽高不超过 512 像素的整数尺寸，因此使用精确子集 DP：
对每个裁片子集求全部“紧密外框”的 Pareto 前沿（二元切分递归覆盖所有直切板），
再用集合划分 DP 求最少页数。允许旋转的裁片取 0 或 90 度，不允许的保持当前方向。
"""
import copy

from image_lab.validation import Invalid

MAX_PIECES = 8
MAX_SHEET = 512
CUT_COLOR = (38, 119, 103, 255)


def piece_extent(q, rotation):
    """返回裁片在给定旋转下的纸面占位 (宽, 高)。"""
    if rotation == 90:
        return q['height'], q['width']
    return q['width'], q['height']


def plan_fingerprint(p):
    """参与自动排版的几何要素指纹；预览后这些要素若变化，旧方案即失效。"""
    parts = [str(p['width']), str(p['height']), str(p['gap'])]
    for q in p['pieces']:
        parts.append('|'.join(str(v) for v in (
            q['id'], q['width'], q['height'], q['rotation'],
            bool(q.get('allow_rotate', True)), q['group'], q['page'], q['x'], q['y'])))
    return '\n'.join(parts)


def _prune(records, width, height):
    """同一子集的紧密外框按宽升序、高降序保留 Pareto 前沿，并剔除超纸面的框。"""
    records = [r for r in records if r['box'][0] <= width and r['box'][1] <= height]
    records.sort(key=lambda r: (r['box'][0], r['box'][1]))
    kept = []
    min_h = None
    for rec in records:
        h = rec['box'][1]
        if min_h is not None and h >= min_h:
            continue
        kept.append(rec)
        min_h = h
    out = []
    seen = set()
    for rec in kept:
        if rec['box'] in seen:
            continue
        seen.add(rec['box'])
        out.append(rec)
    return out


class GuillotineEngine:
    def __init__(self, pieces, width, height, gap):
        self.pieces = pieces
        self.width = width
        self.height = height
        self.gap = gap
        self.n = len(pieces)
        self.size = 1 << self.n
        self.frontiers = [None] * self.size

    def orientations(self, i):
        """裁片 i 可选的 (旋转, 宽, 高)，同尺寸去重，优先保留 0 度。"""
        q = self.pieces[i]
        opts = []
        seen = set()
        rotations = (0, 90) if q.get('allow_rotate', True) else (q['rotation'],)
        for rotation in rotations:
            if rotation not in (0, 90):
                rotation = 0
            w, h = piece_extent(q, rotation)
            if (w, h) in seen:
                continue
            seen.add((w, h))
            opts.append((rotation, w, h))
        return opts

    def solve(self, mask):
        """子集 mask 的紧密外框 Pareto 前沿；空列表表示该子集无法在纸面内直切排版。"""
        if mask == 0:
            return []
        cached = self.frontiers[mask]
        if cached is not None:
            return cached
        records = []
        bits = [i for i in range(self.n) if mask & (1 << i)]
        if len(bits) == 1:
            i = bits[0]
            for rotation, w, h in self.orientations(i):
                records.append({'box': (w, h), 'node': ('leaf', i, rotation)})
        else:
            gap = self.gap
            # 枚举真子集 A，B=mask\\A；只取 A 含最低位的一半划分，避免左右重复
            low = mask & -mask
            rest = mask ^ low
            sub = (rest - 1) & rest  # rest 的真子集，保证 a_mask 严格小于 mask
            while True:
                a_mask = low | sub
                b_mask = mask ^ a_mask
                for left in self.solve(a_mask):
                    lw, lh = left['box']
                    for right in self.solve(b_mask):
                        rw, rh = right['box']
                        # 竖切：左右拼接，切线通道占 gap
                        vw, vh = lw + gap + rw, max(lh, rh)
                        records.append({'box': (vw, vh),
                                        'node': ('v', a_mask, b_mask, left, right)})
                        # 横切：上下拼接
                        hw, hh = max(lw, rw), lh + gap + rh
                        records.append({'box': (hw, hh),
                                        'node': ('h', a_mask, b_mask, left, right)})
                if sub == 0:
                    break
                sub = (sub - 1) & rest
        self.frontiers[mask] = _prune(records, self.width, self.height)
        return self.frontiers[mask]

    def place(self, node, ox, oy, placements, cuts):
        """把切分树放进以 (ox, oy) 为左上的紧密外框，递归收集位置与直切顺序。"""
        kind = node[0]
        if kind == 'leaf':
            _, i, rotation = node
            q = self.pieces[i]
            w, h = piece_extent(q, rotation)
            placements.append({'index': i, 'x': ox, 'y': oy, 'width': w, 'height': h,
                               'rotation': rotation})
            return
        kind, a_mask, b_mask, left, right = node
        lw, lh = left['box']
        rw, rh = right['box']
        gap = self.gap
        if kind == 'v':
            span_w = lw + gap + rw
            span_h = max(lh, rh)
            corridor_l, corridor_r = ox + lw, ox + lw + gap
            cuts.append({'step': len(cuts) + 1, 'direction': '竖切',
                         'at': ox + lw + gap // 2, 'corridor': [corridor_l, corridor_r],
                         'rect': [ox, oy, ox + span_w, oy + span_h]})
            self.place(left['node'], ox, oy, placements, cuts)
            self.place(right['node'], ox + lw + gap, oy, placements, cuts)
        else:
            span_w = max(lw, rw)
            span_h = lh + gap + rh
            corridor_t, corridor_b = oy + lh, oy + lh + gap
            cuts.append({'step': len(cuts) + 1, 'direction': '横切',
                         'at': oy + lh + gap // 2, 'corridor': [corridor_t, corridor_b],
                         'rect': [ox, oy, ox + span_w, oy + span_h]})
            self.place(left['node'], ox, oy, placements, cuts)
            self.place(right['node'], ox, oy + lh + gap, placements, cuts)

    def best_record(self, mask):
        """选一个最“省地方”的紧密外框：先比面积，再比宽、高，保证结果确定。"""
        frontier = self.solve(mask)
        if not frontier:
            return None
        return min(frontier, key=lambda r: (r['box'][0] * r['box'][1],
                                            r['box'][0], r['box'][1]))

    def render_page(self, mask, record, page_no):
        placements, cuts = [], []
        self.place(record['node'], 0, 0, placements, cuts)
        placements.sort(key=lambda z: z['index'])
        box_w, box_h = record['box']
        return {'page': page_no, 'box': [box_w, box_h],
                'placements': placements, 'cuts': cuts}


def _min_page_partition(engine, full_mask, feasible):
    """对可行子集族做集合划分 DP，返回最少页数下确定的页面掩码列表。"""
    inf = engine.n + 1
    dp = [inf] * engine.size
    split = [0] * engine.size
    dp[0] = 0
    for mask in range(1, engine.size):
        if mask & ~full_mask:
            continue
        best = None
        sub = mask
        while sub:
            other = mask ^ sub
            if feasible.get(sub) and dp[other] + 1 < inf:
                cand = (dp[other] + 1, other.bit_count(), sub)
                if best is None or cand < best:
                    best = cand
            sub = (sub - 1) & mask
        if best is not None:
            dp[mask], split[mask] = best[0], best[2]
    if dp[full_mask] == inf:
        return None
    pages, mask = [], full_mask
    while mask:
        sub = split[mask]
        pages.append(sub)
        mask ^= sub
    return pages  # 页面顺序：最后分出的在前


class _MaskView:
    """让分组子集划分复用同一套划分 DP 的轻量适配（仅提供 n 与 size）。"""

    def __init__(self, n):
        self.n = n
        self.size = 1 << n


def build_plan(p, mode):
    """按 mode（save 省纸 / group 整组）生成自动排版方案，不改动 p。"""
    if mode not in ('save', 'group'):
        raise Invalid('排版方式需为 save 或 group')
    pieces = p['pieces']
    if not 1 <= len(pieces) <= MAX_PIECES:
        raise Invalid('自动排版本次只处理1至8个裁片')
    width, height, gap = p['width'], p['height'], p['gap']
    if not (1 <= width <= MAX_SHEET and 1 <= height <= MAX_SHEET):
        raise Invalid('自动排版要求纸面宽高均为1至512像素的整数')

    engine = GuillotineEngine(pieces, width, height, gap)
    full = engine.size - 1
    feasible = {mask: bool(engine.solve(mask)) for mask in range(engine.size)}

    unfit_pieces = [pieces[i]['name'] for i in range(engine.n)
                    if not feasible.get(1 << i)]
    if unfit_pieces:
        raise Invalid('以下裁片在允许的取向下无法放进纸面：' + '、'.join(unfit_pieces))

    if mode == 'save':
        page_masks = _min_page_partition(engine, full, feasible)
        if page_masks is None:
            raise Invalid('当前纸面与间隔下不存在可行的直切排版')
        unfit_groups = []
    else:
        groups = {}
        for i, q in enumerate(pieces):
            groups.setdefault(q['group'], []).append(i)
        group_masks, unfit_groups = {}, []
        for name, idxs in groups.items():
            mask = sum(1 << i for i in idxs)
            if feasible.get(mask):
                group_masks[name] = mask
            else:
                unfit_groups.append(name)
        page_masks = []
        if not unfit_groups:
            # 以“整组不可拆分”为物品再做一次最少页集合划分
            g_feasible = {0: True}
            masks = list(group_masks.values())
            for bits in range(1, 1 << len(masks)):
                union = 0
                for k, gm in enumerate(masks):
                    if bits & (1 << k):
                        union |= gm
                g_feasible[bits] = feasible.get(union, False)
            g_engine = _MaskView(len(masks))
            chosen = _min_page_partition(g_engine, (1 << len(masks)) - 1, g_feasible)
            if chosen is None:
                raise Invalid('当前纸面与间隔下不存在可行的整组直切排版')
            for bits in chosen:
                union = 0
                for k, gm in enumerate(masks):
                    if bits & (1 << k):
                        union |= gm
                page_masks.append(union)

    pages = []
    for page_no, mask in enumerate(page_masks, 1):
        record = engine.best_record(mask)
        pages.append(engine.render_page(mask, record, page_no))

    return {
        'mode': mode,
        'revision': p.get('revision', 0),
        'fingerprint': plan_fingerprint(p),
        'sheet': {'width': width, 'height': height, 'gap': gap},
        'page_count': len(pages),
        'pages': pages,
        'unfit_groups': unfit_groups,
    }


def verify_plan(p, plan, adopt=False):
    """复核方案未过期，且其中每个坐标、旋转、页面都仍然合法。

    adopt=True 时额外要求方案可采纳（省纸/整组成功、没有放不下的组）。
    """
    if not isinstance(plan, dict) or plan.get('fingerprint') != plan_fingerprint(p):
        raise Invalid('预览方案已过期：裁片或纸面已修改，请重新生成预览')
    if plan.get('revision') != p.get('revision', 0):
        raise Invalid('预览方案已过期：排版已被其他操作修改，请重新生成预览')
    if adopt and (plan.get('mode') not in ('save', 'group') or plan.get('unfit_groups')):
        raise Invalid('该方案不能采纳')
    if plan.get('mode') not in ('save', 'group'):
        raise Invalid('排版方案无效')
    expected = [q['id'] for q in p['pieces']]
    seen = []
    for page in plan['pages']:
        for z in page['placements']:
            i = z['index']
            if not 0 <= i < len(p['pieces']):
                raise Invalid('方案裁片索引无效')
            seen.append(p['pieces'][i]['id'])
            q = p['pieces'][i]
            w, h = piece_extent(q, z['rotation'])
            if (w, h) != (z['width'], z['height']):
                raise Invalid('方案裁片尺寸与当前裁片不一致')
            if z['rotation'] == 90 and not q.get('allow_rotate', True):
                raise Invalid('方案旋转了不允许旋转的裁片')
            if z['x'] < 0 or z['y'] < 0 or z['x'] + w > p['width'] or z['y'] + h > p['height']:
                raise Invalid('方案存在越界裁片')
    if sorted(seen) != sorted(expected) or len(seen) != len(expected):
        raise Invalid('方案裁片集合与当前排版不一致')
    _verify_cuts(p, plan)


def _verify_cuts(p, plan):
    """独立复核：每页切线贯穿所在剩余矩形、落在间隔通道内且不穿过裁片。"""
    gap = p['gap']
    for page in plan['pages']:
        placed = page['placements']
        for cut in page['cuts']:
            x0, y0, x1, y1 = cut['rect']
            if cut['direction'] == '竖切':
                if not (x0 <= cut['at'] < x1):
                    raise Invalid('切线未贯穿剩余矩形')
                for z in placed:
                    zx, zy, zw, zh = z['x'], z['y'], z['width'], z['height']
                    if zy < y1 and zy + zh > y0 and zx < cut['at'] < zx + zw:
                        raise Invalid('切线穿过裁片')
            else:
                if not (y0 <= cut['at'] < y1):
                    raise Invalid('切线未贯穿剩余矩形')
                for z in placed:
                    zx, zy, zw, zh = z['x'], z['y'], z['width'], z['height']
                    if zx < x1 and zx + zw > x0 and zy < cut['at'] < zy + zh:
                        raise Invalid('切线穿过裁片')
        # 切线必须落在间隔通道里：切线两侧若都有裁片，间距至少为 gap
        for cut in page['cuts']:
            x0, y0, x1, y1 = cut['rect']
            if cut['direction'] == '竖切':
                lefts = [z for z in placed if z['y'] < y1 and z['y'] + z['height'] > y0
                         and z['x'] + z['width'] <= cut['at']]
                rights = [z for z in placed if z['y'] < y1 and z['y'] + z['height'] > y0
                          and z['x'] > cut['at']]
                if lefts and rights:
                    lmax = max(z['x'] + z['width'] for z in lefts)
                    rmin = min(z['x'] for z in rights)
                    if rmin - lmax < gap:
                        raise Invalid('切线间隔不足')
            else:
                ups = [z for z in placed if z['x'] < x1 and z['x'] + z['width'] > x0
                       and z['y'] + z['height'] <= cut['at']]
                downs = [z for z in placed if z['x'] < x1 and z['x'] + z['width'] > x0
                         and z['y'] > cut['at']]
                if ups and downs:
                    umax = max(z['y'] + z['height'] for z in ups)
                    dmin = min(z['y'] for z in downs)
                    if dmin - umax < gap:
                        raise Invalid('切线间隔不足')


def apply_plan(p, plan):
    """在已 checkpoint 的排版上落地方案：只改页号、坐标与旋转，其余字段保持。"""
    verify_plan(p, plan, adopt=True)
    for page in plan['pages']:
        for z in page['placements']:
            q = p['pieces'][z['index']]
            q['page'] = page['page']
            q['x'] = z['x']
            q['y'] = z['y']
            q['rotation'] = z['rotation']


def render_plan(p, images, plan, page_no=1):
    """按方案渲染单页 RGBA 数组（复用正常渲染管线，再叠加直切线与序号）。"""
    from .core import render
    page = next((v for v in plan['pages'] if v['page'] == page_no), plan['pages'][0])
    staged = copy.deepcopy(p)
    for v in plan['pages']:
        for z in v['placements']:
            q = staged['pieces'][z['index']]
            q['page'] = v['page']
            q['x'], q['y'], q['rotation'] = z['x'], z['y'], z['rotation']
    canvas = render(staged, images, page['page'], guides=False)
    from PIL import Image, ImageDraw
    im = Image.fromarray(canvas, 'RGBA')
    draw = ImageDraw.Draw(im)
    for cut in page['cuts']:
        x0, y0, x1, y1 = cut['rect']
        if cut['direction'] == '竖切':
            _dashed_v(draw, cut['at'], y0, y1)
            bx, by = cut['at'] + 2, y0 + 2
        else:
            _dashed_h(draw, cut['at'], x0, x1)
            bx, by = x0 + 2, cut['at'] + 2
        draw.rectangle((bx, by, bx + 13, by + 12), fill=(255, 255, 255, 235),
                       outline=CUT_COLOR)
        draw.text((bx + 3, by), str(cut['step']), fill=CUT_COLOR)
    import numpy as np
    return np.array(im)


def _dashed_h(draw, y, x0, x1, dash=6):
    x = x0
    while x < x1:
        draw.line([(x, y), (min(x + dash, x1), y)], fill=CUT_COLOR, width=1)
        x += dash * 2


def _dashed_v(draw, x, y0, y1, dash=6):
    y = y0
    while y < y1:
        draw.line([(x, y), (x, min(y + dash, y1))], fill=CUT_COLOR, width=1)
        y += dash * 2
