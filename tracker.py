"""
Consumer Behavior Journal Tracker v4
追加機能: キーワードフィルタリング、Slack通知、config.json管理
"""

import sqlite3, json, os, re, time, urllib.request, urllib.parse, anthropic
from datetime import datetime, timedelta
from pathlib import Path

# ==================== 設定ファイル読み込み ====================

CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "keywords": [
        "social media", "SNS",
        "price", "pricing",
        "sustainability", "green",
        "AI", "artificial intelligence",
        "emotion", "affect",
        "identity", "self-concept"
    ],
    "slack_webhook_url": "",
    "fetch_days": 90,
    "fetch_days_after": 14
}

def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        # 古いconfigにキーが足りない場合はデフォルトで補完
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
    else:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        print(f"📝 config.json を作成しました: {CONFIG_PATH}")
        cfg = DEFAULT_CONFIG.copy()
    # 環境変数があれば優先（GitHub Actions用）
    if os.environ.get("SLACK_WEBHOOK_URL"):
        cfg["slack_webhook_url"] = os.environ["SLACK_WEBHOOK_URL"]
    return cfg

# ==================== ジャーナル定義 ====================

JOURNALS = [
    {"name": "Journal of Consumer Research",  "abbr": "JCR", "issn": "0093-5301", "color": "#534AB7"},
    {"name": "Journal of Marketing Research",  "abbr": "JMR", "issn": "0022-2437", "color": "#993C1D"},
    {"name": "Journal of Marketing",           "abbr": "JM",  "issn": "0022-2429", "color": "#0F6E56"},
    {"name": "Journal of Consumer Psychology", "abbr": "JCP", "issn": "1057-7408", "color": "#854F0B"},
    {"name": "Psychology & Marketing",         "abbr": "P&M", "issn": "0742-6046", "color": "#72243E"},
]

DB_PATH    = Path(__file__).parent / "data" / "papers.db"
OUTPUT_DIR = Path(__file__).parent / "output"

# ==================== キーワードフィルタリング ====================

def match_keywords(paper, keywords):
    """タイトル＋abstractにキーワードが含まれるか判定。マッチしたキーワードリストを返す"""
    if not keywords:
        return []
    text = (paper.get("title","") + " " + paper.get("abstract","")).lower()
    return [kw for kw in keywords if kw.lower() in text]

# ==================== DB ====================

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS papers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, doi TEXT UNIQUE,
        journal TEXT, title TEXT, authors TEXT, abstract TEXT,
        summary_en TEXT, summary_ja TEXT, url TEXT, published TEXT,
        fetched_at TEXT, run_id TEXT, matched_keywords TEXT)""")
    for col in ["run_id TEXT", "matched_keywords TEXT"]:
        try: conn.execute(f"ALTER TABLE papers ADD COLUMN {col}")
        except: pass
    conn.commit()
    return conn

def is_new(conn, doi):
    return conn.execute("SELECT id FROM papers WHERE doi=?", (doi,)).fetchone() is None

def has_any(conn):
    return conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] > 0

def save(conn, p, run_id):
    conn.execute("""INSERT OR IGNORE INTO papers
        (doi,journal,title,authors,abstract,summary_en,summary_ja,url,published,fetched_at,run_id,matched_keywords)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (p["doi"],p["journal"],p["title"],p["authors"],p["abstract"],
         p["summary_en"],p["summary_ja"],p["url"],p["published"],
         datetime.now().isoformat(), run_id,
         json.dumps(p.get("matched_keywords", []), ensure_ascii=False)))
    conn.commit()

# ==================== OpenAlex ====================

def reconstruct(inv):
    if not inv: return ""
    pos = {}
    for w, pl in inv.items():
        for p in pl: pos[p] = w
    return " ".join(pos[i] for i in sorted(pos))

def fetch_journal(journal, since_date):
    issn, abbr = journal["issn"], journal["abbr"]
    since = since_date.strftime("%Y-%m-%d")
    papers, cursor, page = [], "*", 0
    print(f"  📡 {abbr}: {since} 以降を取得中...")
    while True:
        params = {
            "filter": f"primary_location.source.issn:{issn},from_publication_date:{since},has_abstract:true",
            "select": "id,doi,title,authorships,abstract_inverted_index,primary_location,publication_date",
            "sort": "publication_date:desc", "per-page": "50", "cursor": cursor,
            "mailto": "tracker@example.com"
        }
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "JournalTracker/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            print(f"  ⚠️ APIエラー: {e}"); break
        results = data.get("results", [])
        if not results: break
        for item in results:
            abstract = reconstruct(item.get("abstract_inverted_index"))
            if not abstract: continue
            doi = (item.get("doi") or item.get("id","")).replace("https://doi.org/","")
            auths = [(a.get("author") or {}).get("display_name","") for a in (item.get("authorships") or [])[:5]]
            auths = [a for a in auths if a]
            if len(item.get("authorships") or []) > 5: auths.append("et al.")
            loc = item.get("primary_location") or {}
            url_paper = loc.get("landing_page_url") or f"https://doi.org/{doi}"
            papers.append({"doi": doi, "journal": abbr,
                "title": (item.get("title") or "").strip(),
                "authors": ", ".join(auths), "abstract": abstract,
                "url": url_paper, "published": item.get("publication_date",""),
                "summary_en":"","summary_ja":"","matched_keywords":[]})
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not cursor: break
        page += 1
        if page >= 4: break
        time.sleep(0.3)
    print(f"     → {len(papers)} 件取得"); return papers

# ==================== AI ====================

def summarize(client, paper):
    prompt = f"""以下の学術論文を読み、2種類の要約を作成してください。

Journal: {paper['journal']}
Title: {paper['title']}
Authors: {paper['authors']}
Abstract:
{paper['abstract']}

以下のJSON形式のみで回答（他のテキスト不要）:
{{"summary_en": "3-4 sentences covering research question, method, key finding, and contribution.","summary_ja": "研究の問い・方法・主要な発見・貢献を含む3〜4文の日本語要約。"}}"""
    msg = client.messages.create(model="claude-opus-4-5", max_tokens=600,
        messages=[{"role":"user","content":prompt}])
    raw = msg.content[0].text.strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        d = json.loads(m.group(0))
        return d.get("summary_en",""), d.get("summary_ja","")
    return "",""

# ==================== Slack通知 ====================

def send_slack(webhook_url, new_papers, keyword_papers):
    if not webhook_url:
        return
    total = len(new_papers)
    kw_total = len(keyword_papers)

    # ヘッダーブロック
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📚 Journal Tracker — 新着論文レポート"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                "text": f"*{datetime.now().strftime('%Y年%m月%d日')}* の新着: *{total}件*\nうちキーワードマッチ: *{kw_total}件*"}
        },
        {"type": "divider"}
    ]

    # キーワードマッチ論文を先に表示（最大5件）
    if keyword_papers:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "🔍 *キーワードマッチ論文*"}
        })
        for p in keyword_papers[:5]:
            kws = ", ".join(p.get("matched_keywords", []))
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                    "text": f"*[{p['journal']}]* <{p['url']}|{p['title']}>\n"
                            f"_{p['authors']}_\n"
                            f"🏷️ `{kws}`\n"
                            f"{p.get('summary_ja','')}"}
            })
            blocks.append({"type": "divider"})
        if len(keyword_papers) > 5:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"_他 {len(keyword_papers)-5} 件のキーワードマッチ論文はレポートで確認してください_"}
            })

    # その他の新着（最大3件）
    other_papers = [p for p in new_papers if p["doi"] not in {k["doi"] for k in keyword_papers}]
    if other_papers:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "📄 *その他の新着論文*"}
        })
        for p in other_papers[:3]:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                    "text": f"*[{p['journal']}]* <{p['url']}|{p['title']}>\n_{p['authors']}_"}
            })
        if len(other_papers) > 3:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"_他 {len(other_papers)-3} 件はレポートで確認してください_"}
            })

    payload = json.dumps({"blocks": blocks}).encode("utf-8")
    try:
        req = urllib.request.Request(webhook_url, data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"  ✅ Slack通知送信完了 (status: {resp.status})")
    except Exception as e:
        print(f"  ⚠️ Slack通知エラー: {e}")

# ==================== HTML ====================

def make_card(row, show_badge=True):
    journal, title, authors, s_en, s_ja, url, pub, kw_json = row
    color_map = {j["abbr"]:j["color"] for j in JOURNALS}
    color = color_map.get(journal, "#888")
    badge = f'<span class="jbadge" style="background:{color}">{journal}</span>' if show_badge else ""
    # キーワードバッジ
    kws = []
    try: kws = json.loads(kw_json or "[]")
    except: pass
    kw_badges = "".join(f'<span class="kwbadge">{kw}</span>' for kw in kws)
    kw_row = f'<div class="kwrow">{kw_badges}</div>' if kws else ""
    return f"""
    <div class="card {'kw-match' if kws else ''}">
      <div class="card-head">
        <div class="title-row">{badge}<a href="{url}" target="_blank" class="title">{title}</a></div>
        <span class="date">{pub}</span>
      </div>
      <div class="authors">{authors}</div>
      {kw_row}
      <div class="summaries">
        <div class="block"><span class="tag">EN</span><p>{s_en}</p></div>
        <div class="block"><span class="tag">JA</span><p>{s_ja}</p></div>
      </div>
    </div>"""

def generate_report(conn, run_id, keywords):
    latest_rows = conn.execute("""
        SELECT journal,title,authors,summary_en,summary_ja,url,published,matched_keywords
        FROM papers WHERE run_id=? ORDER BY published DESC
    """, (run_id,)).fetchall()

    all_rows = conn.execute("""
        SELECT journal,title,authors,summary_en,summary_ja,url,published,matched_keywords
        FROM papers ORDER BY published DESC LIMIT 300
    """).fetchall()

    kw_rows = [r for r in all_rows if r[7] and json.loads(r[7] or "[]")]

    by_journal = {}
    for r in all_rows: by_journal.setdefault(r[0],[]).append(r)

    color_map = {j["abbr"]:j["color"] for j in JOURNALS}
    name_map  = {j["abbr"]:j["name"]  for j in JOURNALS}

    tabs_html = f'<button class="tab active" onclick="switchTab(\'latest\',this)">🆕 最新追加 <span class="cnt">{len(latest_rows)}</span></button>\n'
    tabs_html += f'<button class="tab" onclick="switchTab(\'keywords\',this)" style="--tc:#d97706">🔍 キーワード <span class="cnt">{len(kw_rows)}</span></button>\n'
    for abbr, papers in by_journal.items():
        color = color_map.get(abbr,"#888")
        tabs_html += f'<button class="tab" onclick="switchTab(\'{abbr}\',this)" style="--tc:{color}">{abbr} <span class="cnt">{len(papers)}</span></button>\n'

    # 最新追加パネル
    panels_html = '<div id="panel-latest" class="panel active">'
    if latest_rows:
        for row in latest_rows: panels_html += make_card(row, show_badge=True)
    else:
        panels_html += '<div class="empty">今回の実行で追加された論文はありません</div>'
    panels_html += '</div>'

    # キーワードパネル
    kw_list_html = "".join(f'<span class="kwbadge lg">{kw}</span>' for kw in keywords)
    panels_html += f'<div id="panel-keywords" class="panel">'
    panels_html += f'<div class="kw-header">監視中のキーワード: {kw_list_html}</div>'
    if kw_rows:
        for row in kw_rows: panels_html += make_card(row, show_badge=True)
    else:
        panels_html += '<div class="empty">キーワードにマッチした論文はまだありません</div>'
    panels_html += '</div>'

    # 雑誌別パネル
    for abbr, papers in by_journal.items():
        color = color_map.get(abbr,"#888")
        full  = name_map.get(abbr, abbr)
        panels_html += f'<div id="panel-{abbr}" class="panel">'
        panels_html += f'<div class="journal-title" style="border-left:4px solid {color};padding-left:12px;margin-bottom:16px"><strong>{full}</strong></div>'
        for row in papers: panels_html += make_card(row, show_badge=False)
        panels_html += '</div>'

    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    total = len(all_rows)

    html = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Journal Tracker</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Helvetica Neue',Arial,sans-serif;background:#f4f6f9;color:#333}}
header{{background:#1a1a2e;color:#fff;padding:20px 32px}}
header h1{{font-size:20px;margin-bottom:2px}}
header p{{font-size:12px;opacity:.6}}
.meta{{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}}
.meta span{{background:rgba(255,255,255,.12);border-radius:6px;padding:4px 12px;font-size:12px}}
.tab-bar{{background:#fff;border-bottom:1px solid #e5e7eb;padding:0 24px;display:flex;gap:2px;overflow-x:auto;position:sticky;top:0;z-index:10}}
.tab{{border:none;background:none;padding:12px 14px;font-size:13px;color:#666;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;transition:color .15s}}
.tab:hover{{color:#333}}
.tab.active{{color:var(--tc,#1a1a2e);border-bottom-color:var(--tc,#1a1a2e);font-weight:600}}
.cnt{{background:#eee;color:#555;font-size:10px;padding:1px 6px;border-radius:10px;margin-left:4px}}
.tab.active .cnt{{background:var(--tc,#1a1a2e);color:#fff;opacity:.9}}
main{{max-width:960px;margin:24px auto;padding:0 20px}}
.panel{{display:none}}.panel.active{{display:block}}
.card{{background:#fff;border-radius:12px;padding:18px 20px;margin-bottom:14px;box-shadow:0 1px 6px rgba(0,0,0,.06);border-left:3px solid transparent}}
.card.kw-match{{border-left-color:#d97706}}
.card-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:6px}}
.title-row{{display:flex;align-items:flex-start;gap:8px;flex:1;min-width:0}}
.jbadge{{font-size:10px;font-weight:700;color:#fff;padding:2px 7px;border-radius:4px;white-space:nowrap;margin-top:2px;flex-shrink:0}}
.title{{font-size:14px;font-weight:600;color:#1a73e8;text-decoration:none;line-height:1.45}}
.title:hover{{text-decoration:underline}}
.date{{font-size:11px;color:#999;white-space:nowrap;flex-shrink:0}}
.authors{{font-size:11px;color:#888;margin-bottom:8px}}
.kwrow{{margin-bottom:10px;display:flex;flex-wrap:wrap;gap:4px}}
.kwbadge{{font-size:11px;background:#fef3c7;color:#92400e;border:1px solid #fcd34d;padding:2px 8px;border-radius:20px}}
.kwbadge.lg{{font-size:12px;padding:4px 10px}}
.kw-header{{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:13px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.summaries{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.block{{background:#f8f9fa;border-radius:8px;padding:12px;position:relative}}
.tag{{position:absolute;top:8px;right:10px;font-size:10px;font-weight:700;background:#ddd;color:#555;padding:1px 5px;border-radius:4px}}
.block p{{font-size:12px;line-height:1.75;color:#444;padding-right:28px}}
.journal-title{{font-size:16px;color:#333}}
.empty{{text-align:center;color:#999;padding:60px 0;font-size:14px}}
@media(max-width:600px){{.summaries{{grid-template-columns:1fr}}.tab-bar{{padding:0 8px}}main{{padding:0 12px}}}}
</style></head>
<body>
<header>
  <h1>📚 Consumer Behavior Journal Tracker</h1>
  <p>消費者行動系ジャーナル 新着論文要約ダッシュボード</p>
  <div class="meta">
    <span>📄 累計 {total} 件</span>
    <span>🆕 今回 {len(latest_rows)} 件</span>
    <span>🔍 KWマッチ {len(kw_rows)} 件</span>
    <span>🕐 {now}</span>
  </div>
</header>
<div class="tab-bar">{tabs_html}</div>
<main>{panels_html}</main>
<script>
function switchTab(id, btn) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('panel-' + id).classList.add('active');
  btn.classList.add('active');
}}
</script>
</body></html>"""

    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / "report.html"
    out.write_text(html, encoding="utf-8")
    return out

# ==================== メイン ====================

def run():
    print("="*52); print("📚 Journal Tracker v4"); print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"); print("="*52)

    config = load_config()
    keywords = config.get("keywords", [])
    slack_url = config.get("slack_webhook_url", "")
    print(f"🔍 キーワード: {', '.join(keywords)}")
    print(f"💬 Slack通知: {'ON' if slack_url else 'OFF（webhook未設定）'}\n")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key: print("❌ ANTHROPIC_API_KEY が設定されていません"); return
    client = anthropic.Anthropic(api_key=api_key)
    conn = init_db()

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    days = config.get("fetch_days_after", 14) if has_any(conn) else config.get("fetch_days", 90)
    since = datetime.now() - timedelta(days=days)
    print(f"📅 取得期間: 過去 {days} 日 ({since.strftime('%Y-%m-%d')} 以降)\n")

    all_new, kw_matched = [], []

    for journal in JOURNALS:
        print(f"\n📰 {journal['name']}")
        try: papers = fetch_journal(journal, since)
        except Exception as e: print(f"  ⚠️ 取得エラー: {e}"); continue

        new_papers = [p for p in papers if is_new(conn, p["doi"])]
        print(f"  🆕 新着: {len(new_papers)} 件")

        for i, paper in enumerate(new_papers, 1):
            # キーワードマッチ判定
            matched = match_keywords(paper, keywords)
            paper["matched_keywords"] = matched
            if matched:
                print(f"  [{i}/{len(new_papers)}] 🔍 KW一致({', '.join(matched)}): {paper['title'][:45]}...")
            else:
                print(f"  [{i}/{len(new_papers)}] {paper['title'][:55]}...")
            try:
                en, ja = summarize(client, paper)
                paper["summary_en"] = en; paper["summary_ja"] = ja
                save(conn, paper, run_id)
                all_new.append(paper)
                if matched: kw_matched.append(paper)
            except Exception as e: print(f"  ⚠️ 要約エラー: {e}")

    print(f"\n📊 今回 {len(all_new)} 件追加 / キーワードマッチ {len(kw_matched)} 件")

    # Slack通知（新着があった時だけ）
    if slack_url and all_new:
        print("💬 Slack通知を送信中...")
        send_slack(slack_url, all_new, kw_matched)
    elif slack_url and not all_new:
        print("💬 新着なし → Slack通知スキップ")

    out = generate_report(conn, run_id, keywords)
    print(f"✅ レポート: {out.resolve()}")
    print(f"🌐 開くには: open '{out.resolve()}'")
    conn.close()

if __name__ == "__main__": run()
