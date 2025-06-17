from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import httpx
import asyncpg
import traceback # Import module traceback
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware # Import CORSMiddleware

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

# Model untuk input rekomendasi alumni individu
class RekomendasiInput(BaseModel):
    nama_lengkap: str
    language: str = "id"  # default Bahasa Indonesia

# Model baru untuk input rekomendasi proyek
class ProyekInput(BaseModel):
    ide_proyek: str # Ini adalah satu kolom isian yang menampung judul dan/atau deskripsi
    language: str = "id"  # default Bahasa Indonesia

async def cari_top_alumni_kolaborasi(current_alumni_id: int, current_alumni_full_profile_text: str):
    """
    Mencari hingga 5 alumni lain yang paling relevan untuk kolaborasi
    berdasarkan keahlian dan detail profil alumni yang sedang diproses.
    """
    # Menambahkan statement_cache_size=0 untuk mengatasi error prepared statement
    conn = await asyncpg.connect(SUPABASE_DB_URL, statement_cache_size=0)
    
    try: # Menambahkan try-except di sini untuk menangkap error database spesifik
        # Ambil semua alumni dari alumni_db kecuali alumni saat ini, termasuk skill_gabungan
        all_alumni_general = await conn.fetch(
            "SELECT id, nama_lengkap, aktivitas, skill_gabungan FROM alumni_db WHERE id != $1", # Menggunakan skill_gabungan
            current_alumni_id
        )

        all_relevant_alumni = [] # Mengganti top_alumni_list sementara dengan daftar semua alumni yang relevan
        
        # Kata kunci dari profil lengkap alumni utama untuk dicocokkan
        current_alumni_keywords = set(current_alumni_full_profile_text.lower().split())

        for alumni_gen in all_alumni_general:
            other_alumni_id = alumni_gen["id"]
            other_alumni_nama = alumni_gen["nama_lengkap"]
            
            # --- START PERUBAHAN UNTUK AKTIVITAS GABUNGAN ALUMNI LAIN ---
            other_alumni_aktivitas_gabungan = alumni_gen["aktivitas"]
            # Pisahkan string aktivitas menjadi list untuk iterasi
            other_alumni_aktivitas_list = [a.strip() for a in other_alumni_aktivitas_gabungan.split(',')] 
            # --- END PERUBAHAN ---

            # Ambil skill_gabungan alumni lain langsung dari alumni_db
            other_alumni_skills_gabungan_from_db = alumni_gen["skill_gabungan"] or ""
            
            detail_parts_other_alumni = []
            # Loop melalui setiap aktivitas yang mungkin dimiliki alumni lain
            for act_sub in other_alumni_aktivitas_list: 
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

            # Gabungkan skill_gabungan dari alumni_db dan detail dari tabel aktivitas menjadi satu string untuk alumni lain
            other_alumni_full_profile_text = " ".join(filter(None, [other_alumni_skills_gabungan_from_db] + detail_parts_other_alumni)).strip().lower()
            
            # Cek relevansi: hitung berapa banyak kata kunci dari profil alumni utama yang cocok dengan profil alumni lain
            match_score = 0
            if other_alumni_full_profile_text:
                for keyword in current_alumni_keywords:
                    if keyword in other_alumni_full_profile_text:
                        match_score += 1
            
            if match_score > 0: # Hanya tambahkan jika ada kecocokan
                # Tambahkan ringkasan yang akan disajikan ke LLM, termasuk nama dan skill relevan
                all_relevant_alumni.append({
                    "nama_alumni_kolaborasi": other_alumni_nama, 
                    "aktivitas": other_alumni_aktivitas_gabungan, # Menyimpan aktivitas gabungan
                    "relevance_skills": other_alumni_skills_gabungan_from_db, # Kirim skill_gabungan dari DB
                    "relevance_detail_summary": other_alumni_full_profile_text, # Kirim ringkasan detail untuk LLM
                    "match_score": match_score # Simpan skor kecocokan
                })
        
        # Urutkan semua alumni yang relevan berdasarkan match_score (tertinggi ke terendah)
        all_relevant_alumni.sort(key=lambda x: x['match_score'], reverse=True)
        
        # Ambil hanya 5 alumni teratas
        top_5_alumni = all_relevant_alumni[:5]

        return top_5_alumni # Mengembalikan top 5 alumni
    finally:
        await conn.close() 

async def ambil_profil_alumni(nama_lengkap: str):
    """
    Mengambil profil lengkap alumni dari database berdasarkan nama lengkap (non-exact match).
    """
    # Menambahkan statement_cache_size=0 untuk mengatasi error prepared statement
    conn = await asyncpg.connect(SUPABASE_DB_URL, statement_cache_size=0)
    
    try: 
        # Menambahkan nama_panggilan dan skill_gabungan ke query SELECT
        # Menggunakan TRIM() untuk menangani spasi di awal/akhir input dan kolom database
        row = await conn.fetchrow("""
            SELECT id, nama_lengkap, nama_panggilan, aktivitas, skill_gabungan
            FROM alumni_db WHERE LOWER(TRIM(nama_lengkap)) = LOWER(TRIM($1))
        """, nama_lengkap)

        if not row:
            raise HTTPException(status_code=404, detail="Alumni tidak ditemukan")

        alumni_id = row["id"]
        # row["aktivitas"] mungkin sekarang adalah string yang digabungkan
        alumni_utama_aktivitas_gabungan = row["aktivitas"] 
        aktivitas_list_utama = [a.strip() for a in alumni_utama_aktivitas_gabungan.split(',')]

        # Mengambil skill_gabungan langsung dari row
        alumni_utama_skills_gabungan = row["skill_gabungan"] or "" 

        detail_alumni_utama = {} # Menggunakan dict untuk detail yang digabungkan
        detail_parts_alumni_utama = [] # Untuk menggabungkan skill_gabungan dan detail alumni utama
        
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

        # Gabungkan skill_gabungan dari alumni_db dan detail dari tabel aktivitas menjadi satu string untuk alumni utama
        current_alumni_full_profile_text = " ".join(filter(None, [alumni_utama_skills_gabungan] + detail_parts_alumni_utama)).strip()

        # Ambil semua peluang lalu filter berdasarkan skill user (perhatikan: fungsi 'cocok' sekarang menggunakan skill_gabungan)
        # Untuk mencocokkan peluang, kita perlu list skill individual dari skill_gabungan
        skills_for_cocok = [s.strip() for s in alumni_utama_skills_gabungan.split(',') if s.strip()]

        # Mengubah fungsi cocok untuk menggunakan skills_for_cocok
        def cocok(row_val):
            if not row_val: return False
            return any(skill.lower() in row_val.lower() for skill in skills_for_cocok) # Menggunakan skills_for_cocok

        bisnis_rows = await conn.fetch("SELECT nama_usaha, dukungan, kolaborasi, butuh_sdm FROM alumni_bisnis") 
        pekerja_rows = await conn.fetch("SELECT skill, deskripsi_skill, sertifikasi, dukungan FROM alumni_pekerja")
        irt_rows = await conn.fetch("SELECT bidang_minat, spesifik_bidang, pengalaman_kelas, perlu_grup FROM alumni_rumah_tangga")
        
        peluang_bisnis = [dict(r) for r in bisnis_rows if cocok(r["dukungan"] or "") or cocok(r["kolaborasi"] or "") or cocok(r["butuh_sdm"] or "")]
        peluang_pekerja = [dict(r) for r in pekerja_rows if cocok(r["skill"] or "") or cocok(r["deskripsi_skill"] or "") or cocok(r["dukungan"] or "")]
        peluang_irt = [dict(r) for r in irt_rows if cocok(r["bidang_minat"] or "") or cocok(r["spesifik_bidang"] or "") or cocok(r["perlu_grup"] or "")]

        # Panggil fungsi baru untuk mencari top 5 alumni kolaborasi
        top_alumni_kolaborasi = await cari_top_alumni_kolaborasi(alumni_id, current_alumni_full_profile_text)

        return {
            "nama": row["nama_lengkap"],
            "nama_panggilan": row["nama_panggilan"],
            "aktivitas": alumni_utama_aktivitas_gabungan, # Kirim aktivitas gabungan ke prompt
            "skills": alumni_utama_skills_gabungan, # Mengirim skill_gabungan sebagai string
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
                # Skip 'skill' if it's already represented by 'Keahlian' field (skills_gabungan)
                if k == 'skill':
                    continue

                # Only include if value is not empty
                if v: 
                    detail_parts.append(f"{k.replace('_', ' ').capitalize()}: {v}")
            if detail_parts: # Only add if there are actual details to show
                formatted_detail_activity = "Detail Aktivitas:\n- " + "\n- ".join(detail_parts) + "\n"
        else: # en
            detail_parts = []
            for k, v in data['detail'].items():
                if k == 'dukungan' and "bekerja" in data['aktivitas'].lower():
                    continue
                if k == 'skill':
                    continue

                if v:
                    detail_parts.append(f"{k.replace('_', ' ').capitalize()}: {v}")
            if detail_parts:
                formatted_detail_activity = "Activity Details:\n- " + "\n- ".join(detail_parts) + "\n"
    # --- END PERBAIKAN FORMAT DETAIL AKTIVITAS ---


    bahasa_id = (
        f"Profil Alumni:\n"
        f"Nama Lengkap: {data['nama']}\n"
        f"Nama Panggilan: {data['nama_panggilan']}\n"
        f"Aktivitas saat ini: {data['aktivitas']}\n" 
        f"Keahlian: {data['skills']}\n" # Ini akan menampilkan skill_gabungan
        f"{formatted_detail_activity}" # Menggunakan string detail yang diformat
        f"{dukungan_dibutuhkan_str}" # Menampilkan dukungan yang dibutuhkan secara eksplisit (jika ada)
        f"\nBerikut adalah peluang nyata dari alumni lain yang membutuhkan dukungan atau kolaborasi:\n" # Tambahkan newline
        f"{peluang_bisnis_str}" 
        f"- Alumni Pekerja: {data['peluang_pekerja']}\n"
        f"- Alumni IRT: {data['peluang_irt']}\n\n"
        # Memindahkan informasi top alumni sebagai konteks, bukan instruksi output bernomor
        f"{top_alumni_kolaborasi_content}\n\n" 
        f"Silakan berikan:\n"
        f"1. Ringkasan profil {data['nama_panggilan']}. Sertakan semua aktivitas dan detail relevan yang digabungkan.\n" # Perjelas instruksi
        f"2. Analisis peluang kolaborasi yang sesuai keahlian {data['nama_panggilan']}. "
        f"Untuk setiap peluang (baik dari alumni bisnis, pekerja, atau IRT), jelaskan bagaimana profil {data['nama_panggilan']} cocok dengan kebutuhan tersebut. " 
        f"Kemudian, identifikasi dan sebutkan nama-nama alumni dari daftar 'profil alumni lain yang paling cocok untuk kolaborasi' yang paling relevan untuk setiap peluang tersebut, serta jelaskan bagaimana mereka dapat terlibat.\n" 
        f"3. Rekomendasi nyata dan profesional untuk kolaborasi atau pengembangan karir berdasarkan data alumni lainnya.\n"
        f"4. Tampilkan minimal 5 contoh **judul atau nama proyek** kolaborasi yang konkrit dan realistis berdasarkan data peluang dari alumni lain dan alumni yang telah Anda ringkas profilnya (sebutkan nama mereka jika relevan), yang bisa dikerjakan bersama {data['nama_panggilan']}.\n"
        f"Tolong gunakan bahasa yang jelas dan profesional. Pastikan untuk selalu merujuk pada alumni utama dengan **nama panggilannya** ({data['nama_panggilan']}) saja, tanpa prefiks 'alumni' atau 'bapak/ibu'."
    )

    bahasa_en = (
        f"Alumni Profile:\n"
        f"Full Name: {data['nama']}\n"
        f"Nickname: {data['nama_panggilan']}\n"
        f"Current Activity: {data['aktivitas']}\n" 
        f"Skills: {data['skills']}\n" # This will display skill_gabungan
        f"{formatted_detail_activity}" # Using formatted detail string
        f"{dukungan_dibutuhkan_str}" # Explicitly display support needed (if any)
        f"\nHere are actual opportunities from other alumni in need of support or collaboration:\n" # Add newline
        f"{peluang_bisnis_str}" 
        f"- Worker Alumni: {data['peluang_pekerja']}\n"
        f"- Homemaker Alumni: {data['peluang_irt']}\n\n"
        # Memindahkan informasi top alumni sebagai konteks, bukan instruksi output bernomor
        f"{top_alumni_kolaborasi_content}\n\n" 
        f"Please provide:\n"
        f"1. A brief profile summary for {data['nama_panggilan']}.\n"
        f"2. Analysis of collaboration opportunities relevant to {data['nama_panggilan']}'s skills. "
        f"For each opportunity (from business, worker, or homemaker alumni), explain how {data['nama_panggilan']}'s profile matches those needs. " 
        f"Then, identify and mention the names of alumni from the 'most suitable alumni profiles for collaboration' list who are most relevant for each opportunity, and explain how they can be involved.\n" 
        f"3. Practical, professional recommendations for collaboration or career advancement based on alumni data.\n"
        f"4. Present at least 5 **concrete and realistic project titles** or collaboration themes derived from available alumni data and the summarized alumni (mention their names if relevant), that {data['nama_panggilan']} could join.\n"
        f"Please use clear and professional language. Always refer to the main alumni by their **nickname** ({data['nama_panggilan']}) only, without prefixes like 'alumni' or 'Mr./Ms.'."
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

# --- START ENDPOINT DAN LOGIKA REKOMENDASI PROYEK BARU ---

async def cari_alumni_untuk_proyek(project_text: str):
    """
    Mencari hingga 10 alumni yang paling relevan untuk suatu proyek
    berdasarkan deskripsi proyek.
    """
    conn = await asyncpg.connect(SUPABASE_DB_URL, statement_cache_size=0)
    
    try:
        # Ambil semua alumni dari alumni_db
        all_alumni_general = await conn.fetch(
            "SELECT id, nama_lengkap, aktivitas, skill_gabungan FROM alumni_db"
        )

        project_keywords = set(project_text.lower().split())
        alumni_candidates = []

        for alumni_gen in all_alumni_general:
            alumni_id = alumni_gen["id"]
            alumni_nama = alumni_gen["nama_lengkap"]
            alumni_aktivitas_gabungan = alumni_gen["aktivitas"]
            alumni_aktivitas_list = [a.strip() for a in alumni_aktivitas_gabungan.split(',')]
            alumni_skills_gabungan = alumni_gen["skill_gabungan"] or ""

            detail_parts_alumni = []
            for act_sub in alumni_aktivitas_list:
                if act_sub == "bekerja":
                    detail = await conn.fetchrow("""
                        SELECT skill, deskripsi_skill, sertifikasi, dukungan
                        FROM alumni_pekerja WHERE alumni_id = $1
                    """, alumni_id)
                    if detail:
                        detail_parts_alumni.extend([detail.get('skill'), detail.get('deskripsi_skill'), detail.get('sertifikasi'), detail.get('dukungan')])
                elif act_sub == "ibu rumah tangga":
                    detail = await conn.fetchrow("""
                        SELECT bidang_minat, spesifik_bidang, pengalaman_kelas, perlu_grup
                        FROM alumni_rumah_tangga WHERE alumni_id = $1
                    """, alumni_id)
                    if detail:
                        detail_parts_alumni.extend([detail.get('bidang_minat'), detail.get('spesifik_bidang'), detail.get('pengalaman_kelas'), detail.get('perlu_grup')])
                elif act_sub == "bisnis / freelance":
                    detail = await conn.fetchrow("""
                        SELECT bidang_usaha, dukungan, kolaborasi, butuh_sdm, skill_praktikal
                        FROM alumni_bisnis WHERE alumni_id = $1
                    """, alumni_id)
                    if detail:
                        detail_parts_alumni.extend([detail.get('bidang_usaha'), detail.get('dukungan'), detail.get('kolaborasi'), detail.get('butuh_sdm'), detail.get('skill_praktikal')])
            
            alumni_full_profile_text = " ".join(filter(None, [alumni_skills_gabungan] + detail_parts_alumni)).strip().lower()

            match_score = 0
            if alumni_full_profile_text:
                for keyword in project_keywords:
                    if keyword in alumni_full_profile_text:
                        match_score += 1
            
            if match_score > 0:
                alumni_candidates.append({
                    "nama_lengkap": alumni_nama,
                    "aktivitas": alumni_aktivitas_gabungan,
                    "skills_gabungan": alumni_skills_gabungan,
                    "full_profile_text": alumni_full_profile_text, # Untuk relevansi ke LLM
                    "match_score": match_score
                })
        
        # Urutkan berdasarkan skor kecocokan, ambil hingga 10 alumni teratas
        alumni_candidates.sort(key=lambda x: x['match_score'], reverse=True)
        return alumni_candidates[:10] # Batasi hingga 10 alumni
    finally:
        await conn.close()


def build_proyek_prompt(proyek_input_data, recommended_alumni, language):
    """
    Membangun prompt untuk LLM berdasarkan ide proyek dan alumni yang direkomendasikan.
    """
    proyek_info = ""
    # Menggunakan input.ide_proyek langsung sebagai deskripsi proyek
    proyek_info += f"Ide Proyek: {proyek_input_data.ide_proyek}\n"

    alumni_list_content = ""
    if recommended_alumni:
        if language.lower() == "id":
            alumni_list_content = "Berikut adalah daftar alumni yang paling relevan dengan ide proyek ini:\n"
            for alumni in recommended_alumni:
                # Menggunakan full_profile_text untuk konteks LLM
                summary = alumni['full_profile_text'] 
                alumni_list_content += f"- Nama: {alumni['nama_lengkap']} (Aktivitas: {alumni['aktivitas'].capitalize()}, Keahlian: {alumni['skills_gabungan']}). Detail Profil: {summary}\n"
        else: # en
            alumni_list_content = "Here is a list of the most relevant alumni for this project idea:\n"
            for alumni in recommended_alumni:
                summary = alumni['full_profile_text']
                alumni_list_content += f"- Name: {alumni['nama_lengkap']} (Activity: {alumni['aktivitas'].capitalize()}, Skills: {alumni['skills_gabungan']}). Profile Details: {summary}\n"
    else:
        alumni_list_content = "Tidak ada alumni yang relevan ditemukan di database untuk proyek ini." if language.lower() == "id" else "No relevant alumni found in the database for this project."

    bahasa_id = (
        f"Anda adalah asisten cerdas yang bertugas merekomendasikan talenta alumni untuk sebuah proyek. "
        f"Berikut adalah informasi proyek yang diajukan:\n"
        f"{proyek_info}\n"
        f"{alumni_list_content}\n\n" # Informasi alumni ditempatkan sebagai konteks umum
        f"Silakan berikan:\n"
        f"1. Deskripsi ringkas gambaran proyeknya dan kebutuhannya.\n"
        f"2. Analisis singkat tentang kebutuhan talenta untuk proyek ini berdasarkan deskripsi proyek.\n"
        f"3. Rekomendasikan hingga 10 alumni dari daftar yang **telah disediakan di atas** yang paling cocok untuk proyek ini, dan tentukan **peran spesifik** yang bisa mereka berikan dalam proyek tersebut (misalnya, \"Lead Data Analyst\", \"Konsultan Bisnis\", \"Content Creator Media Sosial\"), dan berikan **justifikasi singkat** mengapa mereka cocok untuk peran tersebut berdasarkan keahlian dan aktivitas mereka. "
        f"Outputkan rekomendasi alumni ini dalam format **tabel Markdown** yang jelas dengan header sebagai berikut:\n"
        f"| Nama Alumni | Peran yang Direkomendasikan | Justifikasi |\n"
        f"|---|---|---|\n"
        f"(Isi data tabel di bawah baris ini. Jika ada kurang dari 10 alumni yang cocok, sebutkan semua yang cocok.)\n" # Instruksi tambahan untuk mengisi tabel
        f"4. Selesaikan respons Anda dengan pesan penutup yang profesional."
        f"Tolong gunakan bahasa yang jelas, profesional, dan fokus pada peran yang konkret. Pastikan tabel Markdown diformat dengan benar."
    )

    bahasa_en = (
        f"You are a smart assistant tasked with recommending alumni talent for a project. "
        f"Here is the submitted project information:\n"
        f"{proyek_info}\n"
        f"{alumni_list_content}\n\n" # Alumni info placed as general context
        f"Please provide:\n"
        f"1. A brief overview of the project and its needs.\n"
        f"2. A brief analysis of the talent needs for this project based on the project description.\n"
        f"3. Recommend up to 10 alumni from the list **provided above** who are most suitable for this project, and specify their **potential role** in the project (e.g., \"Lead Data Analyst\", \"Business Consultant\", \"Social Media Content Creator\"), and provide a **brief justification** for why they are suitable for that role based on their skills and activities. "
        f"Output these alumni recommendations in a **clear Markdown table** with the following headers:\n"
        f"| Alumni Name | Recommended Role | Justification |\n"
        f"|---|---|---|\n"
        f"(Fill in the table data below this line. If fewer than 10 alumni are suitable, list all suitable ones.)\n" # Additional instruction to fill table
        f"4. Conclude your response with a professional closing message."
        f"Please use clear, professional language, and focus on concrete roles. Ensure the Markdown table is correctly formatted."
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

# --- START ENDPOINT DAN LOGIKA REKOMENDASI PROYEK BARU ---

@app.post("/proyek_rekomendasi")
async def proyek_rekomendasi(input: ProyekInput):
    try:
        # Menggunakan ide_proyek langsung sebagai project_text
        project_text = input.ide_proyek.strip()
        if not project_text: # ide_proyek tidak boleh kosong
            raise HTTPException(status_code=400, detail="Ide proyek tidak boleh kosong.")

        # Cari alumni yang relevan untuk proyek
        recommended_alumni_data = await cari_alumni_untuk_proyek(project_text)
        
        # Bangun prompt untuk LLM
        # Mengirimkan ProyekInput langsung ke build_proyek_prompt
        prompt = build_proyek_prompt(input, recommended_alumni_data, input.language)

        headers = {
            "Content-Type": "application/json"
        }
        gemini_api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

        system_content = {
            "id": "Kamu adalah asisten cerdas yang memberikan rekomendasi talenta alumni dan peran spesifik mereka untuk proyek yang diberikan.",
            "en": "You are a smart assistant providing alumni talent recommendations and their specific roles for a given project."
        }.get(input.language.lower(), "Kamu adalah asisten cerdas yang memberikan rekomendasi talenta alumni dan peran spesifik mereka untuk proyek yang diberikan.")

        body = {
            "contents": [
                {"role": "user", "parts": [{"text": system_content + "\n\n" + prompt}]}
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 2500 # Cukup untuk daftar 10 alumni dengan peran dan justifikasi
            }
        }
        
        async with httpx.AsyncClient(timeout=90.0) as client: # Timeout untuk LLM
            res = await client.post(gemini_api_url, headers=headers, json=body)
            res.raise_for_status()
            content = res.json()["candidates"][0]["content"]["parts"][0]["text"]
            return {"rekomendasi_proyek": content.strip()}

    except HTTPException as e:
        raise e # Re-raise HTTPExceptions (e.g., 400 or 404)
    except Exception as e:
        error_traceback = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}\n\nTraceback:\n{error_traceback}")

# --- END FITUR REKOMENDASI PROYEK BARU ---
