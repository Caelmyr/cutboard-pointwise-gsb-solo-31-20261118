#!/usr/bin/env python3
"""CutBoard 自动直切排版的离线验证脚本（不依赖运行中的服务）。

覆盖：
1. 基础排版、旋转约束、间隔与越界；
2. 每页切分树可被独立的直切模拟逐刀分开，切线不穿裁片；
3. 省纸模式页数相对暴力集合划分最优；
4. 整组模式同组同页、放不下的组明确报错且不拆组；
5. 指纹过期、采纳后一次撤销、确定性与 8 裁片性能。
"""
import copy
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path += ['/opt/pointwise-env/site-packages']

from image_lab.validation import Invalid

from cutboard.autolayout import (
    MAX_PIECES, build_plan, plan_fingerprint, verify_plan, apply_plan,
    GuillotineEngine,
)
from cutboard.core import edit, history, issues, validate


def mk(i, w, h, grp='A组', rot=0, allow=True, page=1, x=0, y=0):
    return dict(id=f'id{i:02d}', image_id=f'img{i:02d}', name=f'卡片{i}', group=grp,
                width=w, height=h, x=x, y=y, page=page, rotation=rot,
                allow_rotate=allow, crop=[0, 0, w, h])


def layout(pieces, width=320, height=240, gap=4):
    p = dict(name='t', width=width, height=height, gap=gap, background='#ffffff',
             pieces=pieces, undo=[], redo=[])
    return validate(p)


def simulate_cuts(page):
    """独立直切模拟：按 step 顺序，每刀必须把某个剩余矩形切成两个非空矩形。"""
    rects = [list(page['cuts'][0]['rect'])] if page['cuts'] else []
    pieces = [(z['x'], z['y'], z['x'] + z['width'], z['y'] + z['height'])
              for z in page['placements']]
    for cut in page['cuts']:
        x0, y0, x1, y1 = cut['rect']
        host = next((r for r in rects
                     if r[0] <= x0 and r[1] <= y0 and x1 <= r[2] and y1 <= r[3]), None)
        if host is None:
            raise AssertionError('切线没有贯穿任何当前剩余矩形')
        at = cut['at']
        if cut['direction'] == '竖切':
            if not (x0 < at < x1):
                raise AssertionError('竖切位置越界')
            a, b = [x0, y0, at, y1], [at, y0, x1, y1]
        else:
            if not (y0 < at < y1):
                raise AssertionError('横切位置越界')
            a, b = [x0, y0, x1, at], [x0, at, x1, y1]
        for (px0, py0, px1, py1) in pieces:
            if cut['direction'] == '竖切' and py0 < y1 and py1 > y0 and px0 < at < px1:
                raise AssertionError('竖切穿过裁片')
            if cut['direction'] == '横切' and px0 < x1 and px1 > x0 and py0 < at < py1:
                raise AssertionError('横切穿过裁片')
        rects.remove(host)
        rects.extend([a, b])
    if not page['cuts']:
        assert len(pieces) == 1
        return
    # 切完后每个剩余矩形应恰好包含一个裁片
    for r in rects:
        inside = [z for z in pieces if r[0] <= z[0] and r[1] <= z[1]
                  and z[2] <= r[2] and z[3] <= r[3]]
        if len(inside) != 1:
            raise AssertionError('切割结束后剩余矩形与裁片不一一对应')
    if len(rects) != len(pieces):
        raise AssertionError('切割矩形数量不等于裁片数量')


def check_plan_geometry(p, plan):
    seen = []
    for page in plan['pages']:
        simulate_cuts(page)
        for z in page['placements']:
            q = p['pieces'][z['index']]
            seen.append(q['id'])
            assert z['x'] >= 0 and z['y'] >= 0
            assert z['x'] + z['width'] <= p['width']
            assert z['y'] + z['height'] <= p['height']
            if z['rotation'] == 90:
                assert q['allow_rotate']
                assert (z['width'], z['height']) == (q['height'], q['width'])
            else:
                assert (z['width'], z['height']) == (q['width'], q['height'])
    assert sorted(seen) == sorted(q['id'] for q in p['pieces'])
    applied = copy.deepcopy(p)
    apply_plan(applied, plan)
    assert issues(applied) == []


def brute_min_pages(p):
    """小 n 暴力：枚举所有集合划分，取可行直切的最少页数（仅 n<=6 调用）。"""
    n = len(p['pieces'])
    engine = GuillotineEngine(p['pieces'], p['width'], p['height'], p['gap'])
    feasible = {m: bool(engine.solve(m)) for m in range(1 << n)}
    best = n + 1
    # 受限增长字符串枚举集合划分
    codes = [0] * n

    def recurse(i, k):
        nonlocal best
        if k >= best:
            return
        if i == n:
            groups = [0] * k
            for idx, c in enumerate(codes):
                groups[c] |= 1 << idx
            if all(feasible[m] for m in groups):
                best = min(best, k)
            return
        for c in range(k + 1):
            codes[i] = c
            recurse(i + 1, max(k, c + 1))

    recurse(0, 0)
    return best


def test_basic_and_rotation():
    p = layout([mk(0, 80, 50), mk(1, 80, 50), mk(2, 60, 90), mk(3, 60, 90)])
    plan = build_plan(p, 'save')
    assert plan['page_count'] == 1
    check_plan_geometry(p, plan)

    # 不允许旋转的裁片必须保持 0 度
    p = layout([mk(0, 140, 60, allow=False), mk(1, 140, 60, allow=False)],
               width=300, height=200)
    plan = build_plan(p, 'save')
    assert all(z['rotation'] == 0 for pg in plan['pages'] for z in pg['placements'])
    check_plan_geometry(p, plan)

    # 只有旋转 90 度才能放下：正向 160x140 高超 100，旋转后 140x160 仍超；
    # 取 160x60：正向宽160 但纸面仅120宽放不下，旋转后 60x160 可放进 120x200
    p = layout([mk(0, 160, 60, allow=True)], width=120, height=200)
    plan = build_plan(p, 'save')
    assert plan['pages'][0]['placements'][0]['rotation'] == 90
    check_plan_geometry(p, plan)

    # 不允许旋转且怎么都放不下 -> 明确报错
    p = layout([mk(0, 160, 60, allow=False)], width=120, height=200)
    try:
        build_plan(p, 'save')
        raise AssertionError('应当报错')
    except Invalid as e:
        assert '无法放进纸面' in str(e)
    print('ok basic_and_rotation')


def test_gap_respected():
    p = layout([mk(i, 50, 50) for i in range(4)], width=108, height=108, gap=4)
    plan = build_plan(p, 'save')
    check_plan_geometry(p, plan)
    # 2x2 紧密外框正好 104=50+4+50
    assert plan['pages'][0]['box'] == [104, 104]
    print('ok gap_respected')


def test_multi_page_optimality():
    random.seed(7)
    for trial in range(40):
        n = random.randint(1, 6)
        pieces = [mk(i, random.randint(40, 140), random.randint(40, 140))
                  for i in range(n)]
        p = layout(pieces, width=random.randint(160, 320),
                   height=random.randint(160, 320), gap=random.randint(0, 8))
        try:
            plan = build_plan(p, 'save')
        except Invalid:
            continue
        check_plan_geometry(p, plan)
        assert plan['page_count'] == brute_min_pages(p), (trial, plan['page_count'])
    print('ok multi_page_optimality (40 random instances vs brute force)')


def test_group_mode():
    pieces = [mk(0, 120, 80, grp='A'), mk(1, 120, 80, grp='A'),
              mk(2, 120, 80, grp='B'), mk(3, 120, 80, grp='B')]
    p = layout(pieces, width=300, height=180, gap=4)
    plan = build_plan(p, 'group')
    assert plan['unfit_groups'] == []
    # 整组约束：每个组的全部裁片必须出现在同一页
    group_pages = {}
    for page in plan['pages']:
        for z in page['placements']:
            g = p['pieces'][z['index']]['group']
            group_pages.setdefault(g, set()).add(page['page'])
    assert all(len(pgs) == 1 for pgs in group_pages.values())
    check_plan_geometry(p, plan)

    # 整组在一张纸上放不下 -> 明确列出，不拆组（单片可放，整组不可放）
    pieces = [mk(0, 200, 150, grp='大组'), mk(1, 200, 150, grp='大组')]
    p = layout(pieces, width=300, height=300, gap=4)
    plan = build_plan(p, 'group')
    assert plan['unfit_groups'] == ['大组']
    assert plan['page_count'] == 0
    try:
        verify_plan(p, plan, adopt=True)
        raise AssertionError('含放不下组的方案不应可采纳')
    except Invalid:
        pass

    # 省纸模式下同一批裁片可以拆到两页
    save = build_plan(p, 'save')
    assert save['page_count'] == 2
    check_plan_geometry(p, save)
    print('ok group_mode')


def test_fingerprint_and_undo():
    p = layout([mk(i, 70, 50) for i in range(3)])
    p['revision'] = 0
    plan = build_plan(p, 'save')
    verify_plan(p, plan)

    stale = copy.deepcopy(p)
    stale['gap'] = 8
    try:
        verify_plan(stale, plan)
        raise AssertionError('改间隔后旧方案应失效')
    except Invalid as e:
        assert '过期' in str(e)

    stale = copy.deepcopy(p)
    stale['pieces'][0]['width'] = 71
    try:
        verify_plan(stale, plan)
        raise AssertionError('改裁片后旧方案应失效')
    except Invalid:
        pass

    applied = copy.deepcopy(p)
    edit(applied, dict(action='autolayout', plan=plan))
    assert len(applied['undo']) == 1  # 一次撤销即可回到原排版
    moved = any(q['x'] != 0 or q['y'] != 0 or q['page'] != 1 for q in applied['pieces'])
    assert moved
    history(applied, 'undo')
    assert applied['pieces'][0]['x'] == 0 and applied['pieces'][0]['page'] == 1
    print('ok fingerprint_and_undo')


def test_determinism_and_capacity():
    p = layout([mk(i, 90, 60) for i in range(MAX_PIECES)], width=260, height=220, gap=4)
    t0 = time.time()
    a = build_plan(p, 'save')
    b = build_plan(p, 'save')
    assert a == b
    check_plan_geometry(p, a)
    elapsed = time.time() - t0
    assert elapsed < 5, f'8 裁片求解过慢: {elapsed:.2f}s'

    # 最坏情况：8 个互异尺寸、允许旋转
    random.seed(11)
    pieces = [mk(i, random.randint(30, 110), random.randint(30, 110))
              for i in range(MAX_PIECES)]
    t0 = time.time()
    plan = build_plan(layout(pieces, width=512, height=512, gap=6), 'save')
    check_plan_geometry(layout(pieces, width=512, height=512, gap=6), plan)
    assert time.time() - t0 < 5
    print(f'ok determinism_and_capacity (8 pieces, {elapsed:.2f}s/{time.time()-t0:.2f}s)')


def test_cut_order_is_guillotine():
    random.seed(3)
    for trial in range(30):
        n = random.randint(2, 8)
        pieces = [mk(i, random.randint(30, 120), random.randint(30, 120))
                  for i in range(n)]
        p = layout(pieces, width=512, height=512, gap=random.randint(0, 10))
        plan = build_plan(p, 'save')
        for page in plan['pages']:
            simulate_cuts(page)
            assert len(page['cuts']) == len(page['placements']) - 1
    print('ok cut_order_is_guillotine (30 random pages)')


def test_scope_limits():
    over = layout([mk(i, 40, 40) for i in range(MAX_PIECES + 1)])
    try:
        build_plan(over, 'save')
        raise AssertionError('超过 8 个裁片应拒绝')
    except Invalid as e:
        assert '1至8个裁片' in str(e)
    big = layout([mk(0, 40, 40)], width=513, height=200)
    try:
        build_plan(big, 'save')
        raise AssertionError('纸面超过 512 应拒绝')
    except Invalid as e:
        assert '512' in str(e)
    print('ok scope_limits')


if __name__ == '__main__':
    test_basic_and_rotation()
    test_gap_respected()
    test_multi_page_optimality()
    test_group_mode()
    test_fingerprint_and_undo()
    test_determinism_and_capacity()
    test_cut_order_is_guillotine()
    test_scope_limits()
    print('全部自动排版测试通过')
