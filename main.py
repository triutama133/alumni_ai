from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import httpx
import asyncpg
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

app = FastAPI()

class RekomendasiInput(BaseModel):
    nama_lengkap: str
    language: str = "id"  # default Bahasa Indonesia

async def ambil_profil_alumni(nama_lengkap: str):
    conn = await asyncpg.connect(SUPABASE_DB_URL)
    row = await conn.fetchrow("""
        SELECT id, nama_lengkap, aktivitas, skill1, skill2, skill3, skill4, skill5, skill6
        FROM alumni_db WHERE LOWER(nama_lengkap) = LOWER($1)
    """, nama_lengkap)

    if not row:
        await conn.close()
        raise HTTPException(status_code=404, detail="Alumni tidak ditemukan")

    alumni_id = row["id"]
    aktivitas = row["aktivitas"]
    skills = [row[f"skill{i}"] for i in range(1, 7) if row[f"skill{i}"]]
    skill_text = ", ".join(skills).lower()

    detail = None
    if aktivitas == "bekerja":
        detail = await conn.fetchrow("""
            SELECT skill, deskripsi_skill, sertifikasi, dukungan
            FROM alumni_pekerja WHERE alumni_id = $1
        """, alumni_id)
    elif aktivitas == "ibu rumah tangga":
        detail = await conn.fetchrow("""
            SELECT bidang_minat, spesifik_bidang, pengalaman_kelas, perlu_grup
            FROM alumni_rumah_tangga WHERE alumni_id = $1
        """, alumni_id)
    elif aktivitas == "bisnis / freelance":
        detail = await conn.fetchrow("""
            SELECT bidang_usaha, dukungan, kolaborasi, butuh_sdm
            FROM alumni_bisnis WHERE alumni_id = $1
        """, alumni_id)

    # Ambil semua peluang lalu filter berdasarkan skill user
    bisnis_rows = await conn.fetch("SELECT dukungan, kolaborasi, butuh_sdm FROM alumni_bisnis")
    pekerja_rows = await conn.fetch("SELECT skill, deskripsi_skill, sertifikasi, dukungan FROM alumni_pekerja")
    irt_rows = await conn.fetch("SELECT bidang_minat, spesifik_bidang, pengalaman_kelas, perlu_grup FROM alumni_rumah_tangga")
    await conn.close()

    def cocok(row_val):
        if not row_val: return False
        return any(skill in row_val.lower() for skill in skills)

    peluang_bisnis = [dict(r) for r in bisnis_rows if cocok(r["dukungan"] or "") or cocok(r["kolaborasi"] or "") or cocok(r["butuh_sdm"] or "")]
    peluang_pekerja = [dict(r) for r in pekerja_rows if cocok(r["skill"] or "") or cocok(r["deskripsi_skill"] or "") or cocok(r["dukungan"] or "")]
    peluang_irt = [dict(r) for r in irt_rows if cocok(r["bidang_minat"] or "") or cocok(r["spesifik_bidang"] or "") or cocok(r["perlu_grup"] or "")]

    return {
        "nama": row["nama_lengkap"],
        "aktivitas": aktivitas,
        "skills": ", ".join(skills),
        "detail": dict(detail) if detail else {},
        "peluang_bisnis": peluang_bisnis,
        "peluang_pekerja": peluang_pekerja,
        "peluang_irt": peluang_irt,
    }

def build_prompt(data, language):
    bahasa_id = (
        f"Profil Alumni:\n"
        f"Nama: {data['nama']}\n"
        f"Aktivitas saat ini: {data['aktivitas']}\n"
        f"Keahlian: {data['skills']}\n"
        f"Detail Aktivitas: {data['detail']}\n\n"
        f"Berikut adalah peluang nyata dari alumni lain yang membutuhkan dukungan atau kolaborasi:\n"
        f"- Alumni Bisnis: {data['peluang_bisnis']}\n"
        f"- Alumni Pekerja: {data['peluang_pekerja']}\n"
        f"- Alumni IRT: {data['peluang_irt']}\n\n"
        f"Silakan berikan:\n"
        f"1. Ringkasan profil alumni ini.\n"
        f"2. Analisis peluang kolaborasi dari alumni lain yang sesuai keahlian dan juga alumni lain yang membutuhkan dukungan sesuai keahliannya.\n"
        f"3. Rekomendasi nyata dan profesional untuk kolaborasi atau pengembangan karir berdasarkan data alumni lainnya.\n"
        f"Tolong gunakan bahasa yang jelas dan profesional. Jangan sebutkan jumlah atau nama alumni lain."
    )

    bahasa_en = (
        f"Alumni Profile:\n"
        f"Name: {data['nama']}\n"
        f"Current Activity: {data['aktivitas']}\n"
        f"Skills: {data['skills']}\n"
        f"Detail: {data['detail']}\n\n"
        f"Here are actual opportunities from other alumni in need of support or collaboration:\n"
        f"- Business Alumni: {data['peluang_bisnis']}\n"
        f"- Worker Alumni: {data['peluang_pekerja']}\n"
        f"- Homemaker Alumni: {data['peluang_irt']}\n\n"
        f"Please provide:\n"
        f"1. A brief profile summary.\n"
        f"2. Analysis of collaboration opportunities relevant to their skills and the others need of support who meet their skills.\n"
        f"3. Practical, professional recommendations for collaboration or career advancement based on alumni data.\n"
        f"Avoid naming or counting other alumni."
    )

    return bahasa_en if language.lower() == "en" else bahasa_id

@app.post("/rekomendasi")
async def rekomendasi(input: RekomendasiInput):
    try:
        data = await ambil_profil_alumni(input.nama_lengkap)
        prompt = build_prompt(data, input.language)

        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }

        system_content = {
            "id": "Kamu adalah asisten cerdas yang memberikan saran karir dan kolaborasi alumni dalam bahasa Indonesia yang profesional.",
            "en": "You are a smart assistant providing alumni career and collaboration suggestions in fluent English."
        }.get(input.language.lower(), "Kamu adalah asisten cerdas yang memberikan saran karir dan kolaborasi alumni dalam bahasa Indonesia.")

        body = {
            "model": "llama3-8b-8192",
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 800
        }

        async with httpx.AsyncClient() as client:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=body)
            res.raise_for_status()
            content = res.json()["choices"][0]["message"]["content"]
            return {"rekomendasi": content.strip()}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))