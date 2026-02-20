"""
ARIA Radiology Workbench — Backend
King's College Hospital NHS Foundation Trust
Clinical AI Programme

FastAPI + SQLite + OpenAI
"""

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import sqlite3, json, csv, io, random, uuid, datetime, secrets, hashlib
import asyncio, os, pathlib
from openai import AsyncOpenAI

app = FastAPI(title="ARIA Radiology Workbench", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

FRONTEND = pathlib.Path(__file__).parent.parent / "frontend"

client = AsyncOpenAI()
DB = "aria.db"
SESSIONS = {}  # token -> username

# ─── DEMO CREDENTIALS ───────────────────────────────────────────
USERS = {
    "david":    hashlib.sha256("kings2024".encode()).hexdigest(),
    "admin":    hashlib.sha256("admin123".encode()).hexdigest(),
    "radiolog": hashlib.sha256("radiology".encode()).hexdigest(),
}

# ─── DATABASE SETUP ─────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS patients (
        patient_id TEXT PRIMARY KEY, mrn TEXT UNIQUE, first_name TEXT, last_name TEXT,
        dob TEXT, sex TEXT, phone TEXT, allergies TEXT, alerts TEXT, infection_flags TEXT
    );
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY, patient_id TEXT, ordering_clinician TEXT,
        ordering_location TEXT, indication TEXT, priority TEXT, status TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS studies (
        study_id TEXT PRIMARY KEY, patient_id TEXT, order_id TEXT,
        modality TEXT, description TEXT, body_part TEXT,
        scheduled_time TEXT, acquisition_time TEXT, accession TEXT,
        report_status TEXT, critical_flag INTEGER, pacs_status TEXT
    );
    CREATE TABLE IF NOT EXISTS series (
        series_id TEXT PRIMARY KEY, study_id TEXT, series_number INTEGER, series_description TEXT
    );
    CREATE TABLE IF NOT EXISTS reports (
        report_id TEXT PRIMARY KEY, study_id TEXT, author TEXT,
        created_at TEXT, updated_at TEXT,
        indication TEXT, technique TEXT, findings TEXT, impression TEXT,
        sign_status TEXT, addenda TEXT
    );
    """)
    db.commit()
    db.close()

init_db()

# ─── AUTH ────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/auth/login")
async def login(req: LoginRequest):
    h = hashlib.sha256(req.password.encode()).hexdigest()
    if req.username.lower() not in USERS or USERS[req.username.lower()] != h:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = secrets.token_hex(32)
    SESSIONS[token] = req.username.lower()
    return {"token": token, "username": req.username.lower(), "message": "Welcome to ARIA"}

@app.post("/auth/logout")
async def logout(token: str):
    SESSIONS.pop(token, None)
    return {"message": "Logged out"}

def verify_token(token: str):
    if token not in SESSIONS:
        raise HTTPException(status_code=401, detail="Unauthorised")
    return SESSIONS[token]

# ─── DATA GENERATION ────────────────────────────────────────────
FIRST_NAMES_F = ["Sarah","Emma","Olivia","Amara","Priya","Fatima","Grace","Charlotte","Ava","Zara","Diane","Helen","Margaret","Aisha","Mei","Rosa","Ingrid","Clare","Patricia","Nadia"]
FIRST_NAMES_M = ["James","Mohammed","David","Oliver","Liam","Ethan","George","Daniel","Kwame","Ahmed","Robert","Michael","Thomas","Samuel","Rajan","Tariq","Patrick","Leon","Victor","Hugo"]
LAST_NAMES = ["Smith","Johnson","Williams","Brown","Jones","Taylor","Davies","Wilson","Evans","Thomas","Roberts","Walker","White","Hall","Martin","Ahmed","Khan","Patel","Singh","Okafor","Chen","Kim","Müller","Rossi","Garcia","Dubois","Andersen","Kowalski","Ferreira","Nguyen"]
CLINICIANS = ["Dr. A. Patel","Dr. S. Rahman","Dr. K. Obi","Dr. L. Chen","Dr. M. Kowalski","Dr. F. Adeyemi","Dr. R. Singh","Dr. T. Williams","Dr. E. Johansson","Dr. N. Mwangi"]
LOCATIONS = ["Emergency Department","Cardiology","Neurology","Oncology","General Medicine","ICU","Orthopaedics","Gastroenterology","Respiratory","Vascular Surgery"]
ALLERGIES_POOL = ["Penicillin","Aspirin","Ibuprofen","Contrast media","Latex","Morphine","Codeine","Sulfonamides","None known"]
ALERTS_POOL = ["Fall risk","DNACPR","Infection control","Bariatric","Implanted device","Pregnancy — confirm","Renal impairment","Contrast caution"]

STUDY_TEMPLATES = [
    ("CT","Head","CT Head without contrast — query intracranial haemorrhage","Urgent"),
    ("CT","Chest","CT Pulmonary Angiography — query pulmonary embolism","Urgent"),
    ("CT","Abdomen","CT Abdomen/Pelvis with contrast — query appendicitis","Urgent"),
    ("CT","Chest","CT Chest — query lung malignancy","Routine"),
    ("CT","Head","CT Head with contrast — query brain metastases","Routine"),
    ("CT","Spine","CT Spine — query cord compression","Urgent"),
    ("CT","Abdomen","CT Abdomen — query aortic aneurysm","Emergency"),
    ("CT","Chest","CT Chest — staging non-small cell lung cancer","Routine"),
    ("CT","Head","CT Head — query subdural haematoma","Emergency"),
    ("CT","Abdomen","CT Abdomen — post-operative review","Routine"),
    ("MRI","Brain","MRI Brain with/without contrast — seizures","Urgent"),
    ("MRI","Brain","MRI Brain — query demyelination / MS","Routine"),
    ("MRI","Spine","MRI Lumbar Spine — radiculopathy","Routine"),
    ("MRI","Cardiac","MRI Cardiac — query cardiomyopathy","Routine"),
    ("MRI","Brain","MRI Brain — query acute stroke","Emergency"),
    ("MRI","Abdomen","MRI Liver — query hepatocellular carcinoma","Routine"),
    ("MRI","Pelvis","MRI Pelvis — staging rectal cancer","Routine"),
    ("MRI","Knee","MRI Knee — query meniscal tear","Routine"),
    ("XR","Chest","Chest X-ray — shortness of breath","Urgent"),
    ("XR","Chest","Chest X-ray — query pneumonia","Urgent"),
    ("XR","Chest","Chest X-ray — query pneumothorax","Emergency"),
    ("XR","Chest","Chest X-ray — routine post-procedure","Routine"),
    ("XR","Abdomen","Abdominal X-ray — query obstruction","Urgent"),
    ("XR","MSK","X-ray Hip — query fracture","Urgent"),
    ("XR","MSK","X-ray Wrist — post-trauma","Routine"),
    ("US","Abdomen","Ultrasound Abdomen — right upper quadrant pain","Urgent"),
    ("US","Pelvis","Ultrasound Pelvis — pelvic pain","Routine"),
    ("US","Neck","Ultrasound Thyroid — query nodule","Routine"),
    ("US","Vascular","Ultrasound Duplex — query DVT","Urgent"),
    ("US","Abdomen","Ultrasound Liver — abnormal LFTs","Routine"),
    ("NM","Chest","Nuclear Medicine V/Q Scan — query PE","Urgent"),
    ("NM","Bone","Nuclear Medicine Bone Scan — staging","Routine"),
    ("NM","Cardiac","Nuclear Medicine Myocardial Perfusion — angina","Routine"),
]

CRITICAL_FINDINGS = [
    "Acute large vessel occlusion — immediate stroke team activation required",
    "Massive pulmonary embolism with right heart strain — urgent cardiology review",
    "Acute aortic dissection type A — immediate cardiothoracic surgery referral",
    "Tension pneumothorax — immediate decompression required",
    "Acute subdural haematoma with midline shift — urgent neurosurgical review",
    "Ruptured abdominal aortic aneurysm — immediate vascular surgery",
    "Cord compression at C4 — immediate neurosurgical referral",
    "New intracranial haemorrhage — neurosurgical review required",
]

REPORT_FINDINGS = {
    "CT": [
        "No acute intracranial abnormality identified. No haemorrhage, mass effect or midline shift. Ventricles and sulci appear normal for age.",
        "There is evidence of a focal consolidative change in the right lower lobe consistent with pneumonia. No pleural effusion. Mediastinum normal.",
        "Filling defect identified within the right main pulmonary artery consistent with acute pulmonary embolism. Evidence of right heart strain.",
    ],
    "MRI": [
        "Multiple periventricular and juxtacortical T2/FLAIR hyperintense lesions identified, the morphology and distribution of which is consistent with demyelinating disease.",
        "No restricted diffusion to suggest acute infarction. No haemorrhage or mass lesion. Background white matter changes consistent with small vessel disease.",
        "Large heterogeneous hepatic lesion in segment 6/7 measuring 4.2 x 3.8cm. Enhancement pattern is consistent with hepatocellular carcinoma.",
    ],
    "XR": [
        "Heart size is at the upper limit of normal. Lung fields are clear. No pleural effusion or pneumothorax. Bony structures intact.",
        "Increased opacification in the left lower zone consistent with consolidation or collapse. Clinical correlation advised.",
        "Gas pattern within normal limits. No evidence of obstruction or perforation. No abnormal calcification.",
    ],
    "US": [
        "The liver is of normal size and echotexture. No focal lesion identified. Gallbladder wall not thickened. No pericholecystic fluid. CBD measures 4mm.",
        "The right common femoral vein is non-compressible with absent flow on colour Doppler, findings consistent with acute deep vein thrombosis.",
        "Normal sized uterus and ovaries. No adnexal mass or free fluid. Endometrial thickness 6mm.",
    ],
    "NM": [
        "Matched ventilation/perfusion defects in the right lower lobe. Low probability for pulmonary embolism.",
        "Increased tracer uptake in multiple vertebral bodies and the right 5th rib, findings consistent with widespread bony metastatic disease.",
    ],
}

def random_dob(min_age=18, max_age=90):
    today = datetime.date.today()
    age = random.randint(min_age, max_age)
    dob = today - datetime.timedelta(days=age*365 + random.randint(0,364))
    return dob.strftime("%Y-%m-%d")

def random_time(days_back=0, hours_back=12):
    base = datetime.datetime.now() - datetime.timedelta(days=days_back, hours=random.randint(0, hours_back))
    return base.strftime("%Y-%m-%d %H:%M")

def generate_patients_and_studies(n_patients=50, studies_per_patient=3, critical_pct=15):
    db = get_db()
    patients_created = 0
    studies_created = 0

    for _ in range(n_patients):
        sex = random.choice(["M","F","M","F","M"])
        fname = random.choice(FIRST_NAMES_M if sex == "M" else FIRST_NAMES_F)
        lname = random.choice(LAST_NAMES)
        pid = str(uuid.uuid4())
        mrn = f"KCH{random.randint(1000000,9999999)}"
        allergies = json.dumps(random.sample(ALLERGIES_POOL, k=random.randint(1,3)))
        alerts = json.dumps(random.sample(ALERTS_POOL, k=random.randint(0,2)))
        try:
            db.execute("INSERT INTO patients VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pid, mrn, fname, lname, random_dob(), sex,
                 f"+44 7{random.randint(100,999)} {random.randint(100000,999999)}",
                 allergies, alerts, json.dumps([])))
            patients_created += 1
        except: continue

        n_studies = random.randint(1, studies_per_patient)
        for _ in range(n_studies):
            tmpl = random.choice(STUDY_TEMPLATES)
            is_critical = random.random() < (critical_pct / 100)
            report_status = random.choice(["Unreported","Unreported","Unreported","Preliminary","Final","Final"])
            oid = str(uuid.uuid4())
            sid = str(uuid.uuid4())
            db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
                (oid, pid, random.choice(CLINICIANS), random.choice(LOCATIONS),
                 f"Query {tmpl[2].split('—')[-1].strip() if '—' in tmpl[2] else tmpl[1]}",
                 tmpl[3], "Active", random_time(days_back=random.randint(0,7))))
            db.execute("INSERT INTO studies VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, pid, oid, tmpl[0], tmpl[2], tmpl[1],
                 random_time(days_back=random.randint(0,3)),
                 random_time(days_back=random.randint(0,2)),
                 f"ACC{random.randint(10000000,99999999)}",
                 report_status, 1 if is_critical else 0, "Available"))

            # Series
            n_series = random.randint(1, 4)
            for sn in range(1, n_series+1):
                db.execute("INSERT INTO series VALUES (?,?,?,?)",
                    (str(uuid.uuid4()), sid, sn,
                     f"Series {sn} — {random.choice(['Axial','Sagittal','Coronal','MIP','Scout'])}"))

            # Report if reported
            if report_status in ["Preliminary","Final"]:
                findings_pool = REPORT_FINDINGS.get(tmpl[0], REPORT_FINDINGS["XR"])
                findings = random.choice(findings_pool)
                if is_critical:
                    findings += f"\n\n⚠ CRITICAL FINDING: {random.choice(CRITICAL_FINDINGS)}"
                db.execute("INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), sid, random.choice(CLINICIANS),
                     random_time(days_back=1), random_time(days_back=0),
                     f"Query {tmpl[1]} pathology", f"{tmpl[0]} {tmpl[1]} {'with contrast' if random.random()>0.5 else 'without contrast'}",
                     findings,
                     "Findings as above. Clinical correlation advised. Please discuss with reporting radiologist if urgent review required.",
                     report_status, json.dumps([])))
            studies_created += 1

    db.commit()
    db.close()
    return {"patients": patients_created, "studies": studies_created}

# ─── API ENDPOINTS ───────────────────────────────────────────────

@app.get("/api/worklist")
async def worklist(
    token: str, modality: str = "", status: str = "", priority: str = "",
    search: str = "", limit: int = 100, offset: int = 0
):
    verify_token(token)
    db = get_db()
    q = """
        SELECT s.study_id, s.modality, s.description, s.body_part, s.scheduled_time,
               s.acquisition_time, s.accession, s.report_status, s.critical_flag, s.pacs_status,
               p.first_name, p.last_name, p.mrn, p.dob, p.sex, p.allergies, p.alerts,
               o.priority, o.ordering_clinician, o.ordering_location, o.indication
        FROM studies s
        JOIN patients p ON s.patient_id = p.patient_id
        JOIN orders o ON s.order_id = o.order_id
        WHERE 1=1
    """
    params = []
    if modality: q += " AND s.modality=?"; params.append(modality)
    if status: q += " AND s.report_status=?"; params.append(status)
    if priority: q += " AND o.priority=?"; params.append(priority)
    if search:
        q += " AND (p.first_name||' '||p.last_name LIKE ? OR p.mrn LIKE ? OR s.description LIKE ?)"
        params.extend([f"%{search}%"]*3)
    q += " ORDER BY s.critical_flag DESC, CASE o.priority WHEN 'Emergency' THEN 0 WHEN 'Urgent' THEN 1 ELSE 2 END, s.scheduled_time DESC"
    q += f" LIMIT {limit} OFFSET {offset}"
    rows = db.execute(q, params).fetchall()
    db.close()
    return {"studies": [dict(r) for r in rows], "total": len(rows)}

@app.get("/api/patient/{patient_id}")
async def get_patient(patient_id: str, token: str):
    verify_token(token)
    db = get_db()
    p = db.execute("SELECT * FROM patients WHERE patient_id=?", [patient_id]).fetchone()
    db.close()
    if not p: raise HTTPException(404, "Patient not found")
    return dict(p)

@app.get("/api/study/{study_id}")
async def get_study(study_id: str, token: str):
    verify_token(token)
    db = get_db()
    s = db.execute("""
        SELECT s.*, p.first_name, p.last_name, p.mrn, p.dob, p.sex, p.allergies, p.alerts,
               o.priority, o.ordering_clinician, o.indication
        FROM studies s
        JOIN patients p ON s.patient_id=p.patient_id
        JOIN orders o ON s.order_id=o.order_id
        WHERE s.study_id=?
    """, [study_id]).fetchone()
    series = db.execute("SELECT * FROM series WHERE study_id=? ORDER BY series_number", [study_id]).fetchall()
    report = db.execute("SELECT * FROM reports WHERE study_id=? ORDER BY created_at DESC LIMIT 1", [study_id]).fetchone()
    db.close()
    if not s: raise HTTPException(404, "Study not found")
    return {"study": dict(s), "series": [dict(x) for x in series], "report": dict(report) if report else None}

@app.post("/api/report")
async def create_report(data: dict, token: str):
    verify_token(token)
    username = SESSIONS[token]
    db = get_db()
    rid = str(uuid.uuid4())
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    db.execute("INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (rid, data["study_id"], username, now, now,
         data.get("indication",""), data.get("technique",""),
         data.get("findings",""), data.get("impression",""),
         data.get("sign_status","Draft"), json.dumps([])))
    db.commit(); db.close()
    return {"report_id": rid, "message": "Report saved"}

@app.get("/api/reports")
async def get_reports(token: str):
    verify_token(token)
    db = sqlite3.connect("aria.db", check_same_thread=False)
    cur = db.execute("""
        SELECT r.*, p.first_name, p.last_name, p.mrn, s.modality, s.body_part, s.accession
        FROM reports r
        JOIN studies s ON r.study_id = s.study_id
        JOIN patients p ON s.patient_id = p.patient_id
        ORDER BY r.updated_at DESC
    """)
    rows = [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]
    db.close()
    return {"reports": rows}

@app.put("/api/report/{report_id}")
async def update_report(report_id: str, data: dict, token: str):
    verify_token(token)
    db = get_db()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    db.execute("""UPDATE reports SET indication=?,technique=?,findings=?,impression=?,
                  sign_status=?,updated_at=? WHERE report_id=?""",
        (data.get("indication",""), data.get("technique",""),
         data.get("findings",""), data.get("impression",""),
         data.get("sign_status","Draft"), now, report_id))
    db.commit(); db.close()
    return {"message": "Report updated"}

@app.post("/api/generate")
async def generate_data(data: dict, token: str):
    verify_token(token)
    result = generate_patients_and_studies(
        n_patients=data.get("patients", 50),
        studies_per_patient=data.get("studies_per_patient", 3),
        critical_pct=data.get("critical_pct", 15)
    )
    return result

@app.get("/api/export")
async def export_data(token: str, fmt: str = "json"):
    verify_token(token)
    db = get_db()
    rows = db.execute("""
        SELECT p.mrn, p.first_name, p.last_name, p.dob, p.sex,
               s.modality, s.description, s.body_part, s.scheduled_time,
               s.report_status, s.critical_flag, o.priority, o.ordering_clinician
        FROM studies s
        JOIN patients p ON s.patient_id=p.patient_id
        JOIN orders o ON s.order_id=o.order_id
        ORDER BY s.scheduled_time DESC
    """).fetchall()
    db.close()
    data = [dict(r) for r in rows]
    if fmt == "csv":
        output = io.StringIO()
        if data:
            writer = csv.DictWriter(output, fieldnames=data[0].keys())
            writer.writeheader(); writer.writerows(data)
        return StreamingResponse(io.BytesIO(output.getvalue().encode()),
            media_type="text/csv", headers={"Content-Disposition":"attachment; filename=aria_worklist.csv"})
    return JSONResponse(data)

@app.post("/api/import")
async def import_data(token: str, file: UploadFile = File(...)):
    verify_token(token)
    content = await file.read()
    imported = 0
    db = get_db()
    try:
        if file.filename.endswith(".json"):
            rows = json.loads(content)
        else:
            reader = csv.DictReader(io.StringIO(content.decode()))
            rows = list(reader)

        for row in rows:
            pid = str(uuid.uuid4())
            mrn = row.get("mrn", f"KCH{random.randint(1000000,9999999)}")
            try:
                db.execute("INSERT INTO patients VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (pid, mrn, row.get("first_name","Unknown"), row.get("last_name","Unknown"),
                     row.get("dob","1970-01-01"), row.get("sex","U"), "", "[]", "[]", "[]"))
                imported += 1
            except: pass
        db.commit()
    except Exception as e:
        raise HTTPException(400, f"Import failed: {str(e)}")
    finally:
        db.close()
    return {"imported": imported}

# ─── ARIA AI ENDPOINTS ───────────────────────────────────────────

@app.post("/api/aria/query")
async def aria_query(data: dict, token: str):
    verify_token(token)
    system = """You are ARIA — Advanced Radiology Intelligence Assistant at King's College Hospital NHS Foundation Trust.
You are a sophisticated, calm, professional female AI assistant embedded in the hospital's radiology workbench.
You assist radiologists and clinicians with workflow queries, study prioritisation, and report assistance.
You never make definitive clinical diagnoses. You always recommend clinical correlation.
Respond concisely and professionally. Maximum 3 sentences unless detail is requested.
Always add: 'This is AI-assisted information — clinical judgment must prevail.'"""
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role":"system","content":system},{"role":"user","content":data.get("query","")}],
            max_tokens=400)
        return {"response": resp.choices[0].message.content}
    except Exception as e:
        return {"response": f"ARIA is temporarily unavailable: {str(e)}"}

@app.post("/api/aria/assist-report")
async def aria_assist_report(data: dict, token: str):
    verify_token(token)
    prompt = f"""You are ARIA, an AI radiology assistant at King's College Hospital.
Based on these findings, suggest a professional radiology report impression and differential diagnosis.

Modality: {data.get('modality','')}
Body Part: {data.get('body_part','')}
Clinical Indication: {data.get('indication','')}
Findings: {data.get('findings','')}

Provide:
1. A concise professional Impression (2-3 sentences)
2. Key differentials (bullet list, max 4)
3. Recommended follow-up if appropriate

IMPORTANT: Prefix with: 'AI-ASSISTED SUGGESTION — Requires radiologist review and sign-off.'"""
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role":"user","content":prompt}],
            max_tokens=500)
        return {"suggestion": resp.choices[0].message.content}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/aria/transcribe")
async def aria_transcribe(audio: UploadFile = File(...), token: str = ""):
    verify_token(token)
    audio_bytes = await audio.read()
    f = io.BytesIO(audio_bytes); f.name = "audio.webm"
    try:
        t = await client.audio.transcriptions.create(model="whisper-1", file=f, language="en")
        return {"transcript": t.text}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/aria/speak")
async def aria_speak(data: dict, token: str):
    verify_token(token)
    voice = data.get("voice", "nova")
    try:
        resp = await client.audio.speech.create(
            model="tts-1", voice=voice, input=data.get("text",""), speed=0.95)
        return StreamingResponse(io.BytesIO(resp.content), media_type="audio/mpeg",
            headers={"Content-Disposition":"inline; filename=aria.mp3"})
    except Exception as e:
        raise HTTPException(500, str(e))

# ─── STATS ───────────────────────────────────────────────────────
@app.get("/api/stats")
async def get_stats(token: str):
    verify_token(token)
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM studies").fetchone()[0]
    critical = db.execute("SELECT COUNT(*) FROM studies WHERE critical_flag=1").fetchone()[0]
    unreported = db.execute("SELECT COUNT(*) FROM studies WHERE report_status='Unreported'").fetchone()[0]
    patients = db.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    mods = db.execute("SELECT modality, COUNT(*) as n FROM studies GROUP BY modality").fetchall()
    db.close()
    return {"total": total, "critical": critical, "unreported": unreported,
            "patients": patients, "modalities": {r[0]:r[1] for r in mods}}

@app.get("/")
async def root():
    index = FRONTEND / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"service":"ARIA Radiology Workbench","trust":"King s College Hospital NHS FT","status":"operational"}

# Serve frontend static files — must be LAST
import os as _os
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="static")

# ─── WEBSOCKET ───────────────────────────────────────────────────
@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json({"event":"heartbeat","timestamp":datetime.datetime.now().isoformat()})
            await asyncio.sleep(10)
    except WebSocketDisconnect:
        pass



if __name__ == "__main__":
    import uvicorn
    # Auto-seed on first run
    db = get_db()
    count = db.execute("SELECT COUNT(*) FROM studies").fetchone()[0]
    db.close()
    if count == 0:
        print("🔄 Seeding initial dataset...")
        result = generate_patients_and_studies(60, 4, 15)
        print(f"✅ Created {result['patients']} patients, {result['studies']} studies")
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
