from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import httpx
import asyncpg
import traceback # Import module traceback
from dotenv import load_dotenv

# Muat variabel lingkungan
load_dotenv()
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
# Ganti dengan variabel lingkungan untuk API Key Gemini Anda
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") 

app = FastAPI()

# Endpoint utama untuk menghindari error 404 pada /
@app.get("/")
def root():
    return {"message": "Alumni AI backend is running!"}

class RekomendasiInput(BaseModel):
    nama_lengkap: str
    language: str = "id"  # default Bahasa Indonesia

async def cari_top_alumni_kolaborasi(current_alumni_id: int, current_alumni_full_profile_text: str):
    """
    Mencari hingga 5 alumni lain yang paling relevan untuk kolaborasi
    berdasarkan keahlian dan detail profil alumni yang sedang diproses.
    """
    # Menambahkan statement_cache_size=0 untuk mengatasi error prepared statement
    conn = await asyncpg.connect(SUPABASE_DB_URL, statement_cache_size=0)
    
    try: # Menambahkan try-except di sini untuk menangkap error database spesifik
        # Ambil semua alumni dari alumni_db kecuali alumni saat ini, termasuk skill mereka
        # Kolom 'aktivitas' mungkin sudah digabungkan menjadi string, e.g., "bekerja, bisnis / freelance"
        all_alumni_general = await conn.fetch(
            "SELECT id, nama_lengkap, aktivitas, skill1, skill2, skill3, skill4, skill5, skill6 FROM alumni_db WHERE id != $1", 
            current_alumni_id
        )

        top_alumni_list = []
        
        # Kata kunci dari profil lengkap alumni utama untuk dicocokkan
        current_alumni_keywords = set(current_alumni_full_profile_text.lower().split())

        for alumni_gen in all_alumni_general:
            other_alumni_id = alumni_gen["id"]
            other_alumni_nama = alumni_gen["nama_lengkap"]
            other_alumni_aktivitas = alumni_gen["aktivitas"] # Ini adalah string gabungan
            # Ambil skill alumni lain langsung dari alumni_db
            other_alumni_skills_from_db = [alumni_gen[f"skill{i}"] for i in range(1, 7) if alumni_gen[f"skill{i}"]]
            
            detail_parts_other_alumni = []
            # Loop melalui setiap aktivitas yang mungkin dimiliki alumni lain
            # Pisahkan string aktivitas menjadi list untuk iterasi
            for act_sub in other_alumni_aktivitas.split(','): 
                act_sub = act_sub.strip()
                if act_sub == "bekerja":
                    detail_other = await conn.fetchrow("""
                        SELECT skill, deskripsi_skill, sertifikasi, dukungan
                        FROM alumni_pekerja WHERE alumni_id = $1
                    """, other_alumni_id)
                    if detail_other:
                        detail_parts_other_alumni.extend([detail_other.get('skill'), detail_other.get('deskripsi_skill'), detail_other.get('sertifikasi'), detail_other.get('dukungan')])
                elif act_sub == "ibu rumah tangga":
                    detail_other = await conn.fetchrow("""
                        SELECT bidang_minat, spesifik_bidang, pengalaman_kelas, perlu_grup
                        FROM alumni_rumah_tangga WHERE alumni_id = $1
                    """, other_alumni_id)
                    if detail_other:
                        detail_parts_other_alumni.extend([detail_other.get('bidang_minat'), detail_other.get('spesifik_bidang'), detail_other.get('pengalaman_kelas'), detail_other.get('perlu_grup')])
                elif act_sub == "bisnis / freelance":
                    detail_other = await conn.fetchrow("""
                        SELECT bidang_usaha, dukungan, kolaborasi, butuh_sdm, skill_praktikal
                        FROM alumni_bisnis WHERE alumni_id = $1
                    """, other_alumni_id)
                    if detail_other:
                        detail_parts_other_alumni.extend([detail_other.get('bidang_usaha'), detail_other.get('dukungan'), detail_other.get('kolaborasi'), detail_other.get('butuh_sdm'), detail_other.get('skill_praktikal')])

            # Gabungkan skill dari alumni_db dan detail dari tabel aktivitas menjadi satu string untuk alumni lain
            other_alumni_full_profile_text = " ".join(filter(None, other_alumni_skills_from_db + detail_parts_other_alumni)).strip().lower()
            
            # Cek relevansi: hitung berapa banyak kata kunci dari alumni utama yang cocok dengan profil alumni lain
            match_score = 0
            if other_alumni_full_profile_text:
                for keyword in current_alumni_keywords:
                    if keyword in other_alumni_full_profile_text:
                        match_score += 1
            
            if match_score > 0: # Hanya tambahkan jika ada kecocokan
                # Tambahkan ringkasan yang akan disajikan ke LLM, termasuk nama dan skill relevan
                top_alumni_list.append({
                    "nama_alumni_kolaborasi": other_alumni_nama, 
                    "aktivitas": other_alumni_aktivitas, # Menyimpan aktivitas asli dari DB (mungkin gabungan)
                    "relevance_skills": ", ".join(filter(None, other_alumni_skills_from_db)), # Kirim skill dari DB
                    "relevance_detail_summary": other_alumni_full_profile_text # Kirim ringkasan detail untuk LLM
                })
                # Batasi 5 di sini seperti sebelumnya untuk efisiensi
                if len(top_alumni_list) >= 5: 
                    break 
        return top_alumni_list 
    finally:
        await conn.close() 

async def ambil_profil_alumni(nama_lengkap: str):
    # Menambahkan statement_cache_size=0 untuk mengatasi error prepared statement
    conn = await asyncpg.connect(SUPABASE_DB_URL, statement_cache_size=0)
    
    try: 
        # Menambahkan nama_panggilan ke query SELECT
        row = await conn.fetchrow("""
            SELECT id, nama_lengkap, nama_panggilan, aktivitas, skill1, skill2, skill3, skill4, skill5, skill6
            FROM alumni_db WHERE LOWER(nama_lengkap) = LOWER($1)
        """, nama_lengkap)

        if not row:
            raise HTTPException(status_code=404, detail="Alumni tidak ditemukan")

        alumni_id = row["id"]
        # row["aktivitas"] mungkin sekarang adalah string yang digabungkan
        alumni_utama_aktivitas_gabungan = row["aktivitas"] 
        aktivitas_list_utama = [a.strip() for a in alumni_utama_aktivitas_gabungan.split(',')]

        skills = [row[f"skill{i}"] for i in range(1, 7) if row[f"skill{i}"]]

        detail_alumni_utama = {} # Menggunakan dict untuk detail yang digabungkan
        detail_parts_alumni_utama = [] # Untuk menggabungkan skill dan detail alumni utama
        
        # Loop melalui setiap aktivitas yang mungkin dimiliki alumni utama
        for act in aktivitas_list_utama:
            if act == "bekerja":
                detail_pekerja = await conn.fetchrow("""
                    SELECT skill, deskripsi_skill, sertifikasi, dukungan
                    FROM alumni_pekerja WHERE alumni_id = $1
                """, alumni_id)
                if detail_pekerja:
                    detail_alumni_utama.update({k: v for k, v in detail_pekerja.items() if v is not None})
                    detail_parts_alumni_utama.extend([detail_pekerja.get('skill'), detail_pekerja.get('deskripsi_skill'), detail_pekerja.get('sertifikasi'), detail_pekerja.get('dukungan')])
            elif act == "ibu rumah tangga":
                detail_irt = await conn.fetchrow("""
                    SELECT bidang_minat, spesifik_bidang, pengalaman_kelas, perlu_grup
                    FROM alumni_rumah_tangga WHERE alumni_id = $1
                """, alumni_id)
                if detail_irt:
                    detail_alumni_utama.update({k: v for k, v in detail_irt.items() if v is not None})
                    detail_parts_alumni_utama.extend([detail_irt.get('bidang_minat'), detail_irt.get('spesifik_bidang'), detail_irt.get('pengalaman_kelas'), detail_irt.get('perlu_grup')])
            elif act == "bisnis / freelance":
                detail_bisnis = await conn.fetchrow("""
                    SELECT bidang_usaha, dukungan, kolaborasi, butuh_sdm, skill_praktikal
                    FROM alumni_bisnis WHERE alumni_id = $1
                """, alumni_id)
                if detail_bisnis:
                    detail_alumni_utama.update({k: v for k, v in detail_bisnis.items() if v is not None})
                    detail_parts_alumni_utama.extend([detail_bisnis.get('bidang_usaha'), detail_bisnis.get('dukungan'), detail_bisnis.get('kolaborasi'), detail_bisnis.get('butuh_sdm'), detail_bisnis.get('skill_praktikal')])

        # Gabungkan skill dari alumni_db dan detail dari tabel aktivitas menjadi satu string untuk alumni utama
        current_alumni_full_profile_text = " ".join(filter(None, skills + detail_parts_alumni_utama)).strip()

        # Ambil semua peluang lalu filter berdasarkan skill user
        bisnis_rows = await conn.fetch("SELECT nama_usaha, dukungan, kolaborasi, butuh_sdm FROM alumni_bisnis") 
        pekerja_rows = await conn.fetch("SELECT skill, deskripsi_skill, sertifikasi, dukungan FROM alumni_pekerja")
        irt_rows = await conn.fetch("SELECT bidang_minat, spesifik_bidang, pengalaman_kelas, perlu_grup FROM alumni_rumah_tangga")
        
        def cocok(row_val):
            if not row_val: return False
            return any(skill.lower() in row_val.lower() for skill in skills)

        peluang_bisnis = [dict(r) for r in bisnis_rows if cocok(r["dukungan"] or "") or cocok(r["kolaborasi"] or "") or cocok(r["butuh_sdm"] or "")]
        peluang_pekerja = [dict(r) for r in pekerja_rows if cocok(r["skill"] or "") or cocok(r["deskripsi_skill"] or "") or cocok(r["dukungan"] or "")]
        peluang_irt = [dict(r) for r in irt_rows if cocok(r["bidang_minat"] or "") or cocok(r["spesifik_bidang"] or "") or cocok(r["perlu_grup"] or "")]

        # Panggil fungsi baru untuk mencari top 5 alumni kolaborasi
        top_alumni_kolaborasi = await cari_top_alumni_kolaborasi(alumni_id, current_alumni_full_profile_text)

        return {
            "nama": row["nama_lengkap"],
            "nama_panggilan": row["nama_panggilan"],
            "aktivitas": alumni_utama_aktivitas_gabungan, # Kirim aktivitas gabungan ke prompt
            "skills": ", ".join(skills),
            "detail": detail_alumni_utama, # Mengirim dict detail yang sudah digabungkan
            "peluang_bisnis": peluang_bisnis,
            "peluang_pekerja": peluang_pekerja,
            "peluang_irt": peluang_irt,
            "top_alumni_kolaborasi": top_alumni_kolaborasi
        }
    finally:
        await conn.close() 

def build_prompt(data, language):
    top_alumni_kolaborasi_content = ""
    if data['top_alumni_kolaborasi']:
        if language.lower() == "id":
            temp_alumni_str_list = []
            for alumni in data['top_alumni_kolaborasi']:
                summary = alumni['relevance_detail_summary'] if alumni['relevance_detail_summary'] else alumni['relevance_skills']
                temp_alumni_str_list.append(f"- Nama: {alumni['nama_alumni_kolaborasi']} (Aktivitas: {alumni['aktivitas'].capitalize()}). Keahlian/Detail Relevan: {summary}")
            top_alumni_kolaborasi_content = "Berikut adalah profil alumni lain yang paling cocok untuk kolaborasi (nama, aktivitas, keahlian, dan detail relevan):\n" + "\n".join(temp_alumni_str_list)
        else: # en
            temp_alumni_str_list = []
            for alumni in data['top_alumni_kolaborasi']:
                summary = alumni['relevance_detail_summary'] if alumni['relevance_detail_summary'] else alumni['relevance_skills']
                temp_alumni_str_list.append(f"- Name: {alumni['nama_alumni_kolaborasi']} (Activity: {alumni['aktivitas'].capitalize()}). Skills/Relevant Details: {summary}")
            top_alumni_kolaborasi_content = "Here are the most suitable alumni profiles for collaboration (name, activity, skills, and relevant details):\n" + "\n".join(temp_alumni_str_list)
    else:
        top_alumni_kolaborasi_content = "Tidak ada alumni lain yang paling cocok ditemukan untuk kolaborasi." if language.lower() == "id" else "No other most suitable alumni found for collaboration."


    # Membuat representasi peluang bisnis agar LLM lebih mudah memprosesnya
    peluang_bisnis_str = ""
    if data['peluang_bisnis']:
        if language.lower() == "id":
            for i, pb in enumerate(data['peluang_bisnis']):
                peluang_bisnis_str += f"- Bisnis '{pb.get('nama_usaha', 'Tidak Diketahui')}' membutuhkan dukungan: {pb.get('dukungan', 'N/A')}, kolaborasi: {pb.get('kolaborasi', 'N/A')}, butuh SDM: {pb.get('butuh_sdm', 'N/A')}. "
                peluang_bisnis_str += f"Gambarkan bagaimana profil {data['nama_panggilan']} sangat cocok untuk kebutuhan ini.\n" 
        else:
             for i, pb in enumerate(data['peluang_bisnis']):
                peluang_bisnis_str += f"- Business '{pb.get('nama_usaha', 'Unknown')}' needs support: {pb.get('dukungan', 'N/A')}, collaboration: {pb.get('kolaborasi', 'N/A')}, human resources: {pb.get('butuh_sdm', 'N/A')}. "
                peluang_bisnis_str += f"Describe how {data['nama_panggilan']}'s profile perfectly matches these needs.\n" 

    # --- START PERUBAHAN UNTUK MENAMPILKAN DUKUNGAN YANG DIBUTUHKAN DI PROFIL ALUMNI UTAMA ---
    dukungan_dibutuhkan_str = ""
    # Periksa apakah alumni utama adalah 'bekerja' (aktivitas bisa digabungkan)
    if "bekerja" in data['aktivitas'].lower():
        if data['detail'] and data['detail'].get('dukungan'):
            if language.lower() == "id":
                dukungan_dibutuhkan_str = f"Dukungan yang dibutuhkan: {data['detail'].get('dukungan')}.\n"
            else:
                dukungan_dibutuhkan_str = f"Support needed: {data['detail'].get('dukungan')}.\n"
    # --- END PERUBAHAN ---

    # --- START PERBAIKAN FORMAT DETAIL AKTIVITAS UNTUK LLM ---
    formatted_detail_activity = ""
    if data['detail']:
        if language.lower() == "id":
            detail_parts = []
            for k, v in data['detail'].items():
                # Skip 'dukungan' if it's already handled by dukungan_dibutuhkan_str for 'bekerja'
                if k == 'dukungan' and "bekerja" in data['aktivitas'].lower():
                    continue
                # Skip 'nama_usaha', 'bidang_usaha' dll if they are for business alumni and already in peluang_bisnis_str
                # For more generalized blending, just ensure common keys from different tables don't clash awkwardly
                # Here, we capitalize and replace underscores for readability
                detail_parts.append(f"{k.replace('_', ' ').capitalize()}: {v}")
            formatted_detail_activity = "Detail Aktivitas:\n- " + "\n- ".join(detail_parts) + "\n"
        else: # en
            detail_parts = []
            for k, v in data['detail'].items():
                if k == 'dukungan' and "bekerja" in data['aktivitas'].lower():
                    continue
                detail_parts.append(f"{k.replace('_', ' ').capitalize()}: {v}")
            formatted_detail_activity = "Activity Details:\n- " + "\n- ".join(detail_parts) + "\n"
    # --- END PERBAIKAN FORMAT DETAIL AKTIVITAS ---


    bahasa_id = (
        f"Profil Alumni:\n"
        f"Nama Lengkap: {data['nama']}\n"
        f"Nama Panggilan: {data['nama_panggilan']}\n"
        f"Aktivitas saat ini: {data['aktivitas']}\n" 
        f"Keahlian: {data['skills']}\n"
        f"{formatted_detail_activity}" # Menggunakan string detail yang diformat
        f"{dukungan_dibutuhkan_str}\n" 
        f"Berikut adalah peluang nyata dari alumni lain yang membutuhkan dukungan atau kolaborasi:\n"
        f"{peluang_bisnis_str}" 
        f"- Alumni Pekerja: {data['peluang_pekerja']}\n"
        f"- Alumni IRT: {data['peluang_irt']}\n\n"
        # Memindahkan informasi top alumni sebagai konteks, bukan instruksi output bernomor
        f"{top_alumni_kolaborasi_content}\n\n" 
        f"Silakan berikan:\n"
        f"1. Ringkasan profil {data['nama_panggilan']}.\n"
        f"2. Analisis peluang kolaborasi yang sesuai keahlian {data['nama_panggilan']}. "
        f"Untuk setiap peluang (baik dari alumni bisnis, pekerja, atau IRT), jelaskan bagaimana profil {data['nama_panggilan']} cocok dengan kebutuhan tersebut. " 
        f"Kemudian, identifikasi dan sebutkan nama-nama alumni dari daftar 'profil alumni lain yang paling cocok untuk kolaborasi' yang paling relevan untuk setiap peluang tersebut, serta jelaskan bagaimana mereka dapat terlibat.\n" 
        f"3. Rekomendasi nyata dan profesional untuk kolaborasi atau pengembangan karir berdasarkan data alumni lainnya.\n"
        f"4. Tampilkan minimal 5 contoh **judul atau nama proyek** kolaborasi yang konkrit dan realistis berdasarkan data peluang dari alumni lain dan alumni yang telah Anda ringkas profilnya (sebutkan nama mereka jika relevan), yang bisa dikerjakan bersama {data['nama_panggilan']}.\n"
        f"Tolong gunakan bahasa yang jelas dan profesional. Pastikan untuk selalu merujuk pada alumni utama dengan **nama panggilannya** ({data['nama_panggilan']}) saja, tanpa prefiks 'alumni' atau 'bapak/ibu'. "
        f"Perlu diingat bahwa {data['nama_panggilan']} mungkin memiliki beberapa aktivitas yang berbeda (bekerja, bisnis, ibu rumah tangga) dan detailnya sudah digabungkan. Analisis Anda harus mencerminkan multi-aktivitas ini."
    )

    bahasa_en = (
        f"Alumni Profile:\n"
        f"Full Name: {data['nama']}\n"
        f"Nickname: {data['nama_panggilan']}\n"
        f"Current Activity: {data['aktivitas']}\n" 
        f"Skills: {data['skills']}\n"
        f"{formatted_detail_activity}" # Using formatted detail string
        f"{dukungan_dibutuhkan_str}\n" 
        f"Here are actual opportunities from other alumni in need of support or collaboration:\n"
        f"{peluang_bisnis_str}" 
        f"- Worker Alumni: {data['peluang_pekerja']}\n"
        f"- Homemaker Alumni: {data['peluang_irt']}\n\n"
        # Moving top alumni info as context, not a numbered output instruction
        f"{top_alumni_kolaborasi_content}\n\n" 
        f"Please provide:\n"
        f"1. A brief profile summary for {data['nama_panggilan']}.\n"
        f"2. Analysis of collaboration opportunities relevant to {data['nama_panggilan']}'s skills. "
        f"For each opportunity (from business, worker, or homemaker alumni), explain how {data['nama_panggilan']}'s profile matches those needs. " 
        f"Then, identify and mention the names of alumni from the 'most suitable alumni profiles for collaboration' list who are most relevant for each opportunity, and explain how they can be involved.\n" 
        f"3. Practical, professional recommendations for collaboration or career advancement based on alumni data.\n"
        f"4. Present at least 5 **concrete and realistic project titles** or collaboration themes derived from available alumni data and the summarized alumni (mention their names if relevant), that {data['nama_panggilan']} could join.\n"
        f"Please use clear and professional language. Always refer to the main alumni by their **nickname** ({data['nama_panggilan']}) only, without prefixes like 'alumni' or 'Mr./Ms.'. "
        f"Note that {data['nama_panggilan']} may have multiple different activities (worker, business, homemaker) and their details have been combined. Your analysis should reflect this multi-activity profile."
    )

    return bahasa_en if language.lower() == "en" else bahasa_id

@app.post("/rekomendasi")
async def rekomendasi(input: RekomendasiInput):
    try:
        data = await ambil_profil_alumni(input.nama_lengkap)
        prompt = build_prompt(data, input.language)

        headers = {
            "Content-Type": "application/json"
        }
        # Tambahkan API Key ke URL untuk Gemini
        gemini_api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

        system_content = {
            "id": "Kamu adalah asisten cerdas yang memberikan saran karir dan kolaborasi alumni dalam bahasa Indonesia yang profesional.",
            "en": "You are a smart assistant providing alumni career and kolaborasi suggestions in fluent English."
        }.get(input.language.lower(), "Kamu adalah asisten cerdas yang memberikan saran karir dan kolaborasi alumni dalam bahasa Indonesia.")

        body = {
            "contents": [
                {"role": "user", "parts": [{"text": system_content + "\n\n" + prompt}]}
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 2500 
            }
        }
        
        async with httpx.AsyncClient(timeout=90.0) as client:
            res = await client.post(gemini_api_url, headers=headers, json=body)
            res.raise_for_status()
            # Parsing respons Gemini API
            content = res.json()["candidates"][0]["content"]["parts"][0]["text"]
            return {"rekomendasi": content.strip()}

    except Exception as e:
        # Menambahkan detail traceback ke respons error untuk debugging yang lebih baik
        error_traceback = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}\n\nTraceback:\n{error_traceback}")
