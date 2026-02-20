content = open(r'E:\Claude\Dev\aria\backend\main.py', 'r', encoding='utf-8').read()

new_endpoint = """
@app.get("/api/reports")
async def get_reports(token: str, db: sqlite3.Connection = Depends(get_db)):
    verify_token(token)
    cur = db.execute(\"\"\"
        SELECT r.*, p.first_name, p.last_name, p.mrn, s.modality, s.body_part, s.accession
        FROM reports r
        JOIN studies s ON r.study_id = s.study_id
        JOIN patients p ON s.patient_id = p.patient_id
        ORDER BY r.updated_at DESC
    \"\"\")
    rows = [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]
    return {"reports": rows}

"""

content = content.replace('if __name__ == "__main__":', new_endpoint + 'if __name__ == "__main__":')
open(r'E:\Claude\Dev\aria\backend\main.py', 'w').write(content)
print('Done')