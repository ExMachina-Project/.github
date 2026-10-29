#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重新生成组织主页的数据面板（snapshot / language / activity / swarm）。

数据来源：
  · 结构类指标（LOC / 文件数 / 编成 / 协议 / 剧本 / 技能 / 视图 / crate）
    —— 直接扫描 ExMachina 源码检出目录；
  · 发行版 / 提交 / 语言字节
    —— GitHub REST API（用 GITHUB_TOKEN 认证）。

设计口径与 profile/assets 里手绘的面板保持一致：1000px 画布、#0B0D10 底、
1px #20262F 描边、直角、等宽字体只用于数字与技术标签、强调色 #7C8CFF。

用法：
  python scripts/gen_profile_assets.py --src .exm-src --out profile/assets
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

# ------------------------------------------------------------------ 设计常量
W, PAD = 1000, 48
L, R = PAD, W - PAD
BG = "#0B0D10"
BORDER = "#20262F"
INK = "#F2F5F9"
INK2 = "#9AA3B0"
INK3 = "#59626F"
ACCENT = "#7C8CFF"
ACCENT2 = "#A9B4FF"
TRACK = "#161B22"

MONO = ("ui-monospace,'SFMono-Regular','SF Mono',Menlo,Consolas,"
        "'Liberation Mono','Courier New',monospace")

API = "https://api.github.com"
ORG = os.environ.get("GITHUB_REPOSITORY_OWNER", "ExMachina-Project")
FLAGSHIP = "ExMachina"
TOKEN = os.environ.get("GITHUB_TOKEN", "")


# ------------------------------------------------------------------ GitHub API
def api(path: str, *, paginate: bool = False, limit: int = 10):
    """调用 REST API；paginate=True 时按页抓取（最多 limit 页）。"""
    items = []
    page = 1
    while True:
        url = f"{API}{path}{'&' if '?' in path else '?'}per_page=100&page={page}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}" if TOKEN else "",
            "User-Agent": "exm-profile-bot",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 409, 451):      # 空仓库 / 不可见
                return [] if paginate else None
            raise
        if not paginate:
            return data
        if not isinstance(data, list) or not data:
            break
        items.extend(data)
        if len(data) < 100 or page >= limit:
            break
        page += 1
    return items


def org_repos() -> list[str]:
    """参与提交统计的仓库。

    排除 .github 本身——它是主页仓库，每次更新面板都会产生提交，
    计入会形成「自己给自己加数」的反馈回路。
    """
    repos = api(f"/orgs/{ORG}/repos?type=public&sort=pushed") or []
    return [r["name"] for r in repos
            if not r.get("archived") and not r.get("fork") and r["name"] != ".github"]



def commit_dates(repo: str) -> list[dt.date]:
    rows = api(f"/repos/{ORG}/{repo}/commits", paginate=True) or []
    out = []
    for row in rows:
        try:
            stamp = row["commit"]["author"]["date"] or row["commit"]["committer"]["date"]
            out.append(dt.date.fromisoformat(stamp[:10]))
        except (KeyError, TypeError):
            continue
    return out


def releases(repo: str) -> list[dt.date]:
    rows = api(f"/repos/{ORG}/{repo}/releases") or []
    out = []
    for row in rows:
        stamp = row.get("published_at")
        if stamp:
            out.append(dt.date.fromisoformat(stamp[:10]))
    return sorted(out)


def languages(repo: str) -> dict[str, int]:
    return api(f"/repos/{ORG}/{repo}/languages") or {}


# ------------------------------------------------------------------ 源码统计
def sh(args: list[str], cwd: pathlib.Path) -> str:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          check=True).stdout


def scan_source(src: pathlib.Path) -> dict:
    """扫描 ExMachina 检出目录，得到结构类指标。"""
    files = [line for line in sh(["git", "ls-files"], src).splitlines() if line]

    rust_lines = 0
    for rel in files:
        if not rel.endswith(".rs") or rel.startswith(("node_modules/", "target/")):
            continue
        try:
            with open(src / rel, "rb") as fh:
                rust_lines += fh.read().count(b"\n")
        except OSError:
            pass

    group = src / "entities" / "groups" / "exmachina"
    agents = sorted(p.stem for p in (group / "agents").glob("*.json")) if (group / "agents").is_dir() else []
    protocols = [p for p in (group / "protocol").glob("*.md") if p.stem.upper() != "README"] \
        if (group / "protocol").is_dir() else []
    playbooks = list((group / "playbooks").glob("*.json")) if (group / "playbooks").is_dir() else []
    skills = list((src / "entities" / "skills").glob("*.json")) if (src / "entities" / "skills").is_dir() else []
    views = list((src / "webui" / "src" / "views").glob("*.tsx")) if (src / "webui" / "src" / "views").is_dir() else []
    crates = [p for p in (src / "crates").iterdir() if p.is_dir()] if (src / "crates").is_dir() else []

    return {
        "rust_lines": rust_lines,
        "files": len(files),
        "agents": agents,
        "protocols": len(protocols),
        "playbooks": len(playbooks),
        "skills": len(skills),
        "views": len(views),
        "crates": len(crates),
    }


# ------------------------------------------------------------------ SVG 工具
def head(w: int, h: int, label: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img" aria-label="{label}">'
            f'<defs><clipPath id="card"><rect x="0" y="0" width="{w}" height="{h}"/></clipPath></defs>'
            f'<rect x="0.5" y="0.5" width="{w-1}" height="{h-1}" fill="{BG}" stroke="{BORDER}"/>')


def grid(w: int, h: int, step: int = 40, op: str = "0.020") -> str:
    out = ['<g clip-path="url(#card)" shape-rendering="crispEdges">']
    x = step
    while x < w:
        out.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}" stroke="#FFFFFF" stroke-opacity="{op}"/>')
        x += step
    y = step
    while y < h:
        out.append(f'<line x1="0" y1="{y}" x2="{w}" y2="{y}" stroke="#FFFFFF" stroke-opacity="{op}"/>')
        y += step
    out.append('</g>')
    return "".join(out)


def panel_header(left: str, right: str, y: int = 46) -> str:
    s = [f'<g font-family="{MONO}">']
    s.append(f'<text x="{L}" y="{y}" font-size="9.6" letter-spacing="3" fill="{INK3}">{left}</text>')
    if right:
        s.append(f'<text x="{R}" y="{y}" font-size="8.8" letter-spacing="2" fill="{INK3}" text-anchor="end">{right}</text>')
    s.append('</g>')
    s.append(f'<line x1="{L}" y1="{y+16}" x2="{R}" y2="{y+16}" stroke="#FFFFFF" stroke-opacity="0.08"/>')
    return "".join(s)


def fmt(n: int) -> str:
    return f"{n:,}"


# ------------------------------------------------------------------ snapshot
def build_snapshot(stats: dict, release_count: int, commit_count: int, today: dt.date) -> str:
    w, h = W, 300
    data = [
        (fmt(stats["rust_lines"]), "LINES OF RUST"),
        (fmt(len(stats["agents"])), "AGENTS"),
        (fmt(stats["protocols"]), "PROTOCOLS"),
        (fmt(stats["playbooks"]), "PLAYBOOKS"),
        (fmt(stats["skills"]), "SKILLS"),
        (fmt(stats["files"]), "FILES"),
        (fmt(stats["crates"]), "CRATES"),
        (fmt(stats["views"]), "UI VIEWS"),
        (fmt(release_count), "RELEASES"),
        (fmt(commit_count), "COMMITS"),
    ]
    s = [head(w, h, label="Snapshot"), grid(w, h)]
    s.append(panel_header("// SNAPSHOT", f"AS OF {today.isoformat()}"))

    colw = (R - L) / 5.0
    rows_y = [136, 230]
    for k in range(1, 5):
        x = L + colw * k
        s.append(f'<line x1="{x:.1f}" y1="78" x2="{x:.1f}" y2="272" stroke="#FFFFFF" stroke-opacity="0.06"/>')
    s.append(f'<line x1="{L}" y1="182" x2="{R}" y2="182" stroke="#FFFFFF" stroke-opacity="0.06"/>')

    for i, (num, lab) in enumerate(data):
        cx = L + colw * (i % 5) + colw / 2
        y = rows_y[i // 5]
        s.append(f'<text x="{cx:.1f}" y="{y}" text-anchor="middle" font-family="{MONO}" font-size="39" '
                 f'font-weight="600" letter-spacing="-1.2" fill="{INK}">{num}</text>')
        s.append(f'<text x="{cx:.1f}" y="{y+24}" text-anchor="middle" font-family="{MONO}" font-size="8" '
                 f'letter-spacing="2.2" fill="{INK3}">{lab}</text>')
    s.append('</svg>')
    return "".join(s)


# ------------------------------------------------------------------ swarm
def build_swarm(agents: list[str]) -> str:
    names = [a[:-6] if a.endswith("-agent") else a for a in agents]
    names.sort(key=lambda n: (n != "orchestrator", n))

    cols = 5
    rows = max(1, math.ceil(len(names) / cols))
    w, h = W, 100 + 32 * rows
    hot_idx = next((i for i, n in enumerate(names) if n == "orchestrator"), -1)
    right = (f"1 ORCHESTRATOR + {len(names) - 1} UNITS" if hot_idx >= 0
             else f"{len(names)} UNITS")

    s = [head(w, h, label="Swarm manifest"), grid(w, h)]
    s.append(panel_header("// SWARM MANIFEST", right))

    colw = (R - L) / cols
    top, step = 100, 32
    for k in range(1, cols):
        x = L + colw * k
        s.append(f'<line x1="{x:.1f}" y1="78" x2="{x:.1f}" y2="{h-28}" stroke="#FFFFFF" stroke-opacity="0.05"/>')

    for i, name in enumerate(names):
        c, r = i % cols, i // cols
        x = L + colw * c + 14
        y = top + step * r
        hot = (i == hot_idx)
        s.append(f'<text x="{x}" y="{y}" font-family="{MONO}" font-size="8" letter-spacing="1.4" '
                 f'fill="{ACCENT if hot else INK3}">{i+1:02d}</text>')
        s.append(f'<text x="{x+25}" y="{y}" font-family="{MONO}" font-size="11" '
                 f'fill="{ACCENT2 if hot else INK2}">{name}</text>')
        if r < rows - 1:
            s.append(f'<line x1="{x-14}" y1="{y+10}" x2="{x+colw-28}" y2="{y+10}" '
                     f'stroke="#FFFFFF" stroke-opacity="0.035"/>')
    s.append('</svg>')
    return "".join(s)


# ------------------------------------------------------------------ language
def build_language(langs: dict[str, int]) -> str | None:
    if not langs:
        return None
    total = sum(langs.values())
    ordered = sorted(langs.items(), key=lambda kv: -kv[1])
    ramp = ["#F2F5F9", "#AEB7C4", "#7C8695", "#525C6A", "#333B46"]

    rows = [(name.upper(), n, ramp[i]) for i, (name, n) in enumerate(ordered[:4])]
    rest = sum(n for _, n in ordered[4:])
    if rest:
        rows.append(("OTHER", rest, ramp[4]))

    w, h = W, 286
    s = [head(w, h, label="Language composition"), grid(w, h)]
    s.append(panel_header("// LANGUAGE COMPOSITION",
                          f"{total / 1_000_000:.2f} MB · {len(langs)} LANGUAGES"))

    x0, x1 = 166, 800
    ys = [110 + 36 * i for i in range(len(rows))]
    for (name, n, col), y in zip(rows, ys):
        pct = n / total * 100
        bw = max((x1 - x0) * n / total, 3)
        s.append(f'<text x="{L}" y="{y+4}" font-family="{MONO}" font-size="9.8" letter-spacing="1.8" '
                 f'fill="{INK2}">{name}</text>')
        s.append(f'<rect x="{x0}" y="{y-4.5}" width="{x1-x0}" height="9" fill="{TRACK}"/>')
        s.append(f'<rect x="{x0}" y="{y-4.5}" width="{bw:.1f}" height="9" fill="{col}"/>')
        s.append(f'<text x="872" y="{y+4}" text-anchor="end" font-family="{MONO}" font-size="8.8" '
                 f'letter-spacing="1" fill="{INK3}">{n/1_000_000:.2f} MB</text>')
        s.append(f'<text x="{R}" y="{y+4}" text-anchor="end" font-family="{MONO}" font-size="10.5" '
                 f'fill="{INK}">{pct:.1f}%</text>')
    s.append('</svg>')
    return "".join(s)


# ------------------------------------------------------------------ activity
def build_activity(dates: list[dt.date], rel_dates: list[dt.date], today: dt.date) -> str | None:
    if not dates:
        return None
    counts: dict[dt.date, int] = {}
    for d in dates:
        counts[d] = counts.get(d, 0) + 1

    first, last = min(dates), max(dates)
    total = len(dates)
    ymax = max(4, math.ceil(total / 4) * 4)

    x_start = first - dt.timedelta(days=14)
    x_end = max(last, today)
    span = max(1, (x_end - x_start).days)

    w, h = W, 322
    X0, X1 = 72, R
    Y0, Y1 = 248, 92

    def X(d: dt.date) -> float:
        return X0 + (d - x_start).days / span * (X1 - X0)

    def Y(v: float) -> float:
        return Y0 - v / ymax * (Y0 - Y1)

    s = [head(w, h, label="Commit velocity"), grid(w, h)]
    s.append(panel_header("// COMMIT VELOCITY", f"{first.isoformat()} → {last.isoformat()}"))

    steps = 4
    for i in range(steps + 1):
        v = ymax * i / steps
        y = Y(v)
        op = "0.10" if i == 0 else "0.05"
        s.append(f'<line x1="{X0}" y1="{y:.1f}" x2="{X1}" y2="{y:.1f}" stroke="#FFFFFF" stroke-opacity="{op}"/>')
        s.append(f'<text x="{X0-10}" y="{y+3.2:.1f}" text-anchor="end" font-family="{MONO}" '
                 f'font-size="8" fill="{INK3}">{int(v)}</text>')

    # 月份刻度：跨度过大时抽稀
    months = []
    cur = dt.date(x_start.year, x_start.month, 1)
    while cur <= x_end:
        months.append(cur)
        cur = dt.date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    stride = max(1, math.ceil(len(months) / 8))
    for i, d in enumerate(months):
        if i % stride:
            continue
        x = X(d)
        if x < X0 - 1:
            continue
        s.append(f'<line x1="{x:.1f}" y1="{Y0}" x2="{x:.1f}" y2="{Y0+6}" stroke="#FFFFFF" stroke-opacity="0.14"/>')
        s.append(f'<text x="{x:.1f}" y="{Y0+23}" text-anchor="middle" font-family="{MONO}" font-size="8" '
                 f'letter-spacing="1.6" fill="{INK3}">{d.strftime("%b").upper()}</text>')

    # 周刻度
    d = x_start
    while d <= x_end:
        if d.day != 1:
            x = X(d)
            s.append(f'<line x1="{x:.1f}" y1="{Y0}" x2="{x:.1f}" y2="{Y0+3}" stroke="#FFFFFF" stroke-opacity="0.08"/>')
        d += dt.timedelta(days=7)

    # 累计阶梯
    pts, cum = [], 0
    for d in sorted(counts):
        cum += counts[d]
        pts.append((d, cum))

    path = [f'M{X0:.1f} {Y0:.1f}']
    for d, v in pts:
        path.append(f'H{X(d):.1f}V{Y(v):.1f}')
    path.append(f'H{X1:.1f}')
    line = "".join(path)
    s.append(f'<path d="{line}V{Y0:.1f}Z" fill="{INK}" fill-opacity="0.05"/>')
    s.append(f'<path d="{line}" fill="none" stroke="{INK}" stroke-width="1.5"/>')

    for d, v in pts:
        s.append(f'<circle cx="{X(d):.1f}" cy="{Y(v):.1f}" r="2" fill="{BG}" stroke="{INK2}" stroke-width="1.1"/>')

    for rd in rel_dates:
        if rd < x_start:
            continue
        v = max([v for d, v in pts if d <= rd], default=0)
        s.append(f'<circle cx="{X(rd):.1f}" cy="{Y(v):.1f}" r="4.6" fill="{ACCENT}" fill-opacity="0.16"/>')
        s.append(f'<circle cx="{X(rd):.1f}" cy="{Y(v):.1f}" r="2.2" fill="{ACCENT}"/>')

    lx, ly = X(pts[-1][0]), Y(pts[-1][1])
    s.append(f'<text x="{lx+6:.1f}" y="{ly-12:.1f}" text-anchor="end" font-family="{MONO}" font-size="9.5" '
             f'letter-spacing="1.2" fill="{ACCENT2}">{total}</text>')

    s.append(f'<g font-family="{MONO}" font-size="8" letter-spacing="1.8" fill="{INK3}">'
             f'<circle cx="{X0+4}" cy="{h-24}" r="2.2" fill="{ACCENT}"/>'
             f'<text x="{X0+13}" y="{h-21}">RELEASE × {len(rel_dates)}</text>'
             f'<circle cx="{X0+108}" cy="{h-24}" r="2" fill="{BG}" stroke="{INK2}" stroke-width="1.1"/>'
             f'<text x="{X0+117}" y="{h-21}">COMMIT × {total}</text>'
             f'<text x="{X1}" y="{h-21}" text-anchor="end">CUMULATIVE</text></g>')
    s.append('</svg>')
    return "".join(s)


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="ExMachina 源码检出目录")
    ap.add_argument("--out", required=True, help="SVG 输出目录")
    args = ap.parse_args()

    src = pathlib.Path(args.src).resolve()
    out = pathlib.Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if not (src / "Cargo.toml").is_file():
        print(f"error: {src} 不像 ExMachina 仓库根目录", file=sys.stderr)
        return 1

    today = dt.datetime.now(dt.timezone.utc).date()

    stats = scan_source(src)
    print("结构指标:", json.dumps({k: v for k, v in stats.items() if k != "agents"},
                                ensure_ascii=False))
    print(f"编成: {len(stats['agents'])} 个")

    rel = releases(FLAGSHIP)
    print(f"发行版: {len(rel)} 个")

    all_dates: list[dt.date] = []
    for repo in org_repos():
        got = commit_dates(repo)
        all_dates.extend(got)
        print(f"  {repo}: {len(got)} 次提交")

    langs = languages(FLAGSHIP)

    written = []
    targets = {
        "snapshot.svg": build_snapshot(stats, len(rel), len(all_dates), today),
        "swarm.svg": build_swarm(stats["agents"]),
        "language.svg": build_language(langs),
        "activity.svg": build_activity(all_dates, rel, today),
    }
    for name, svg in targets.items():
        if not svg:
            print(f"skip {name}（数据为空）")
            continue
        try:                                     # 发出去之前先确认是合法 SVG
            ET.fromstring(svg)
        except ET.ParseError as exc:
            print(f"error: {name} 不是合法 XML：{exc}", file=sys.stderr)
            return 1
        (out / name).write_text(svg, encoding="utf-8")
        written.append(name)

    print("已写入:", ", ".join(written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
