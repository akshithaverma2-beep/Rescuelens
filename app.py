"""RescueLens - flood & emergency response assistant (HackB 2K26 starter)
Run:  pip install -r requirements.txt  &&  streamlit run app.py
Optional AI triage: set ANTHROPIC_API_KEY (falls back to keyword rules without it).
"""
import base64, json, math, os, random, sqlite3, time

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

DB = "rescuelens.db"
BALLARI = (15.1394, 76.9214)
SEV_W = {"critical": 40, "high": 25, "medium": 12, "low": 4}
SEV_COLOR = {"critical": "red", "high": "orange", "medium": "blue", "low": "green"}
CATEGORIES = ["trapped people", "flooded road", "structure damage", "medical", "supplies", "other"]

# ---------- database ----------
def db():
    con = sqlite3.connect(DB, check_same_thread=False)
    con.execute("""CREATE TABLE IF NOT EXISTS reports(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, name TEXT,
        lat REAL, lon REAL, people INTEGER, text TEXT,
        category TEXT, severity TEXT, reason TEXT, status TEXT DEFAULT 'open')""")
    return con

def add_report(name, lat, lon, people, text, category, severity, reason, ts=None):
    con = db()
    con.execute("INSERT INTO reports(ts,name,lat,lon,people,text,category,severity,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                (ts or time.time(), name, lat, lon, people, text, category, severity, reason))
    con.commit()

# ---------- AI triage ----------
KEYWORDS = {
    "critical": ["trapped", "drowning", "roof", "collapsed", "unconscious", "swept", "not breathing"],
    "high": ["water rising", "children", "elderly", "injured", "pregnant", "stuck", "no exit"],
    "medium": ["flooded road", "blocked", "no power", "no food", "no water"],
}

def rule_triage(text):
    t = text.lower()
    for sev, words in KEYWORDS.items():
        hit = [w for w in words if w in t]
        if hit:
            cat = "trapped people" if sev == "critical" else ("flooded road" if "road" in t else "other")
            return {"severity": sev, "category": cat, "reason": f"keywords: {', '.join(hit)}"}
    return {"severity": "low", "category": "other", "reason": "no urgent keywords"}

def ai_triage(text, image_bytes=None):
    """Use Claude if a key is available, otherwise rules. Always returns a dict."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return rule_triage(text)
    try:
        import anthropic
        client = anthropic.Anthropic()
        content = []
        if image_bytes:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                            "data": base64.b64encode(image_bytes).decode()}})
        content.append({"type": "text", "text":
            "You triage flood emergency reports. Report text: " + repr(text) +
            "\nReply with ONLY JSON: {\"severity\": critical|high|medium|low, "
            f"\"category\": one of {CATEGORIES}, \"reason\": short sentence}}"})
        r = client.messages.create(model="claude-sonnet-5-5", max_tokens=300,
                                   messages=[{"role": "user", "content": content}])
        out = json.loads(r.content[0].text.strip().strip("`").replace("json", "", 1))
        if out.get("severity") in SEV_W:
            return out
    except Exception as e:
        print("AI triage failed, using rules:", e)
    return rule_triage(text)

# ---------- priority ranking ----------
def km(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))

def ranked():
    df = pd.read_sql("SELECT * FROM reports WHERE status='open'", db())
    if df.empty:
        return df
    now = time.time()
    scores = []
    for _, r in df.iterrows():
        near = sum(1 for _, o in df.iterrows() if o.id != r.id and km(r.lat, r.lon, o.lat, o.lon) < 0.5)
        wait_min = (now - r.ts) / 60
        scores.append(SEV_W[r.severity] + min(r.people, 20) + 3 * near + min(wait_min / 10, 10))
    df["priority"] = [round(s, 1) for s in scores]
    return df.sort_values("priority", ascending=False)

# ---------- alerts ----------
def alert_text(area):
    en = f"FLOOD ALERT: High flood risk near {area}. Move to higher ground now. Call 112 for help."
    kn = f"ಪ್ರವಾಹ ಎಚ್ಚರಿಕೆ: {area} ಬಳಿ ಪ್ರವಾಹದ ಅಪಾಯ ಹೆಚ್ಚಿದೆ. ದಯವಿಟ್ಟು ಈಗಲೇ ಎತ್ತರದ ಸ್ಥಳಕ್ಕೆ ಹೋಗಿ. ಸಹಾಯಕ್ಕಾಗಿ 112 ಗೆ ಕರೆ ಮಾಡಿ."
    return en, kn

# ---------- demo data ----------
DEMO = ["Family trapped on roof, water rising fast", "Road flooded near bridge, vehicles stuck",
        "Elderly woman injured, cannot move", "No drinking water since morning",
        "Wall collapsed, people may be trapped", "Children stuck on first floor, water rising",
        "Power cut and no food for 2 days", "Small stream overflowing onto lane"]

def seed(n=15):
    for _ in range(n):
        text = random.choice(DEMO)
        t = rule_triage(text)
        add_report("Demo", BALLARI[0] + random.uniform(-0.05, 0.05), BALLARI[1] + random.uniform(-0.05, 0.05),
                   random.randint(1, 8), text, t["category"], t["severity"], t["reason"],
                   ts=time.time() - random.randint(0, 3600))

# ---------- UI ----------
st.set_page_config(page_title="RescueLens", page_icon="🛟", layout="wide")
st.title("🛟 RescueLens")
page = st.sidebar.radio("View", ["Citizen SOS", "Responder Dashboard"])

if page == "Citizen SOS":
    st.subheader("Send an emergency report")
    with st.form("sos"):
        name = st.text_input("Your name")
        text = st.text_area("What is happening?", placeholder="e.g. Water rising, 3 people on roof")
        people = st.number_input("People affected", 1, 200, 1)
        c1, c2 = st.columns(2)
        lat = c1.number_input("Latitude", value=BALLARI[0], format="%.5f")
        lon = c2.number_input("Longitude", value=BALLARI[1], format="%.5f")
        photo = st.file_uploader("Photo (optional)", type=["jpg", "jpeg", "png"])
        sent = st.form_submit_button("Send SOS", type="primary")
    if sent and text.strip():
        t = ai_triage(text, photo.getvalue() if photo else None)
        add_report(name or "Anonymous", lat, lon, int(people), text, t["category"], t["severity"], t["reason"])
        st.success(f"Report received. Triage: {t['severity'].upper()} ({t['category']}). Help is being prioritised.")

else:
    st.subheader("Responder dashboard")
    b1, b2 = st.columns(2)
    if b1.button("Load demo reports"):
        seed(); st.rerun()
    if b2.button("Clear all"):
        db().execute("DELETE FROM reports").connection.commit(); st.rerun()

    df = ranked()
    if df.empty:
        st.info("No open reports. Load demo reports or submit one from Citizen SOS.")
    else:
        m = folium.Map(location=BALLARI, zoom_start=12)
        for rank, (_, r) in enumerate(df.iterrows(), 1):
            folium.Marker([r.lat, r.lon], tooltip=f"#{rank} {r.severity.upper()}",
                          popup=f"{r.text}<br>People: {r.people}<br>Priority: {r.priority}",
                          icon=folium.Icon(color=SEV_COLOR[r.severity])).add_to(m)
        st_folium(m, height=420, use_container_width=True)

        st.markdown("#### Priority list")
        st.dataframe(df[["priority", "severity", "category", "people", "text", "reason"]],
                     hide_index=True, use_container_width=True)

        st.markdown("#### Mark as handled")
        pick = st.selectbox("Report", df.id, format_func=lambda i: f"#{i}: {df[df.id == i].text.iloc[0][:50]}")
        if st.button("Mark handled"):
            con = db(); con.execute("UPDATE reports SET status='done' WHERE id=?", (int(pick),)); con.commit(); st.rerun()

        st.markdown("#### Area alert")
        area = st.text_input("Area name", "Tungabhadra riverside, Ballari")
        en, kn = alert_text(area)
        st.code(en); st.code(kn)
