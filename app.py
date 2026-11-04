from flask import Flask, render_template, request, flash, redirect, url_for, session
import os
import re
import csv
import json
import sqlite3
import pdfplumber
from docx import Document
import webbrowser
import threading
from datetime import datetime
from uuid import uuid4
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from translations import TRANSLATIONS

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "resume-skill-extractor-dev-key")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024


def get_upload_folder():
    upload_folder = os.environ.get("UPLOAD_FOLDER")
    if upload_folder:
        return upload_folder

    candidate = os.path.join(os.getcwd(), "static", "uploads")
    if os.path.isdir(os.getcwd()) and os.access(os.getcwd(), os.W_OK):
        return candidate

    return os.path.join("/tmp", "resume-skill-extractor", "uploads")


def get_database_path():
    database_path = os.environ.get("DATABASE_PATH")
    if database_path:
        return database_path

    candidate = os.path.join(os.getcwd(), "resume_history.db")
    if os.path.isdir(os.getcwd()) and os.access(os.getcwd(), os.W_OK):
        return candidate

    return os.path.join("/tmp", "resume_history.db")


UPLOAD_FOLDER = get_upload_folder()
DATABASE_PATH = get_database_path()
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf", "docx"}
SKILLS_FILE = "skills.csv"


def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            filename TEXT NOT NULL,
            role_name TEXT,
            score INTEGER NOT NULL,
            skills TEXT NOT NULL,
            job_matches TEXT NOT NULL,
            jd_analysis TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


@app.context_processor
def inject_translations():
    language = session.get("language", "en")

    return {
        "t": TRANSLATIONS.get(
            language,
            TRANSLATIONS["en"]
        )
    }

@app.route("/set-language/<language>")
def set_language(language):
    if language in TRANSLATIONS:
        session["language"] = language

    return redirect(request.referrer or url_for("home"))


def register_user(username, password):
    if not username or not password:
        return False

    normalized_name = username.strip()
    if len(normalized_name) < 3 or len(password) < 6:
        return False

    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (normalized_name, generate_password_hash(password), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def authenticate_user(username, password):
    if not username or not password:
        return False

    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    user = conn.execute(
        "SELECT * FROM users WHERE username = ?",
        (username.strip(),),
    ).fetchone()
    conn.close()

    if not user:
        return False

    return check_password_hash(user["password_hash"], password)


def login_required(view_func):
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to continue.")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    wrapped.__name__ = view_func.__name__
    return wrapped


def normalize_skill_text(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def load_skills():
    with open(SKILLS_FILE, newline="", encoding="utf-8") as skills_file:
        reader = csv.DictReader(skills_file)
        return [row["skill"].strip() for row in reader if row.get("skill", "").strip()]


skills_db = load_skills()


def extract_skills_from_text(text):
    normalized_text = normalize_skill_text(text)
    found_skills = []

    for skill in skills_db:
        normalized_skill = normalize_skill_text(skill)
        if not normalized_skill:
            continue
        if normalized_skill in normalized_text and skill not in found_skills:
            found_skills.append(skill)

    return found_skills


def calculate_jd_match(resume_text, job_description):
    required_skills = extract_skills_from_text(job_description)

    if not required_skills:
        return {
            "required_skills": [],
            "matched_skills": [],
            "missing_skills": [],
            "match_percentage": 0,
        }

    resume_skills = extract_skills_from_text(resume_text)
    matched_skills = [skill for skill in required_skills if skill in resume_skills]
    missing_skills = [skill for skill in required_skills if skill not in resume_skills]
    match_percentage = round((len(matched_skills) / len(required_skills)) * 100) if required_skills else 0

    return {
        "required_skills": required_skills,
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "match_percentage": match_percentage,
    }


def calculate_ats_score(resume_text, job_description="", selected_role=None):
    text_lower = resume_text.lower()

    if job_description:
        target_skills = extract_skills_from_text(job_description)
    elif selected_role and selected_role in job_roles:
        target_skills = job_roles[selected_role]
    else:
        target_skills = extract_skills_from_text(text_lower) if text_lower else []

    resume_skills = extract_skills_from_text(resume_text)
    matched_skills = [skill for skill in target_skills if skill in resume_skills]
    missing_skills = [skill for skill in target_skills if skill not in resume_skills]

    keyword_score = round((len(matched_skills) / len(target_skills)) * 100) if target_skills else 100

    required_sections = ["experience", "education", "skills", "project", "certification"]
    section_hits = sum(1 for section in required_sections if section in text_lower)
    section_score = round((section_hits / len(required_sections)) * 100)

    formatting_score = 100
    if re.search(r"\|\|\|\s*", resume_text) or re.search(r"\b[A-Z]{2,}\s*:\s*$", resume_text):
        formatting_score -= 15
    if len(resume_text.strip()) < 250:
        formatting_score -= 20
    if "\t" in resume_text:
        formatting_score -= 10
    formatting_score = max(0, formatting_score)

    clarity_score = 90 if any(word in text_lower for word in ["experience", "education", "skills"]) else 70

    ats_score = round(keyword_score * 0.45 + section_score * 0.25 + formatting_score * 0.2 + clarity_score * 0.1)

    recommendations = []
    if missing_skills:
        recommendations.append(f"Add the missing keywords: {', '.join(missing_skills[:5])}.")
    if "skills" not in text_lower:
        recommendations.append("Create a dedicated Skills section with clear technical keywords.")
    if "experience" not in text_lower:
        recommendations.append("Add detailed work experience with dates, role titles, and responsibilities.")
    if "education" not in text_lower:
        recommendations.append("Include an Education section using standard headings like Education or Qualifications.")
    if formatting_score < 80:
        recommendations.append("Use a simple, ATS-friendly layout with clean headings and no text boxes or graphics.")
    if not recommendations:
     language = session.get("language", "en")

    default_recommendations = {
        "en": "Your resume structure is already ATS-friendly. Keep it concise and keyword-aligned.",
        "kn": "ನಿಮ್ಮ ರೆಸ್ಯೂಮ್ ರಚನೆಯು ಈಗಾಗಲೇ ATS ಸ್ನೇಹಿಯಾಗಿದೆ. ಅದನ್ನು ಸಂಕ್ಷಿಪ್ತವಾಗಿ ಮತ್ತು ಸೂಕ್ತ ಕೀವರ್ಡ್‌ಗಳೊಂದಿಗೆ ಇರಿಸಿ.",
        "hi": "आपके रिज़्यूमे की संरचना पहले से ही ATS-अनुकूल है। इसे संक्षिप्त और उपयुक्त कीवर्ड के साथ रखें।"
    }

    recommendations.append(default_recommendations.get(language, default_recommendations["en"]))
    return {
        "ats_score": ats_score,
        "keyword_score": keyword_score,
        "section_score": section_score,
        "formatting_score": formatting_score,
        "clarity_score": clarity_score,
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "recommendations": recommendations[:4],
    }


def save_analysis_record(filename, role_name, score, skills, job_matches, jd_analysis=None):
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute(
        """
        INSERT INTO analyses (created_at, filename, role_name, score, skills, job_matches, jd_analysis)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            filename,
            role_name,
            int(score),
            json.dumps(skills),
            json.dumps(job_matches),
            json.dumps(jd_analysis) if jd_analysis else None,
        ),
    )
    conn.commit()
    conn.close()


def get_analysis_history():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM analyses ORDER BY id DESC LIMIT 10"
    ).fetchall()
    conn.close()

    history = []
    for row in rows:
        entry = dict(row)
        entry["skills"] = json.loads(entry["skills"]) if entry.get("skills") else []
        entry["job_matches"] = json.loads(entry["job_matches"]) if entry.get("job_matches") else {}
        entry["jd_analysis"] = json.loads(entry["jd_analysis"]) if entry.get("jd_analysis") else None
        history.append(entry)

    return history


# Job roles and required skills
job_roles = {
    "Software Developer": [
        "Python", "Java", "SQL", "Git", "DBMS"
    ],

    "Python Developer": [
        "Python", "Flask", "SQL", "MySQL", "Git"
    ],

    "Full Stack Developer": [
        "HTML", "CSS", "JavaScript",
        "React", "Node.js", "SQL"
    ],

    "Data Analyst": [
        "Python", "SQL", "Excel",
        "Power BI", "Pandas"
    ],

    "Data Scientist": [
        "Python", "SQL", "Machine Learning",
        "Pandas", "NumPy"
    ]
}


# Home page
@app.route("/")
def home():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if authenticate_user(username, password):
            conn = sqlite3.connect(DATABASE_PATH)
            conn.row_factory = sqlite3.Row
            user = conn.execute("SELECT id, username FROM users WHERE username = ?", (username,)).fetchone()
            conn.close()

            if user:
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                flash("Welcome back!")
                return redirect(url_for("home"))

        flash("Invalid username or password.")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not password:
            flash("Username and password are required.")
        elif len(username) < 3:
            flash("Username must be at least 3 characters long.")
        elif len(password) < 6:
            flash("Password must be at least 6 characters long.")
        elif password != confirm_password:
            flash("Passwords do not match.")
        elif not register_user(username, password):
            flash("That username is already taken.")
        else:
            flash("Registration successful. Please log in.")
            return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


# Upload resume
@app.route("/upload", methods=["POST"])
@login_required
def upload():

    file = request.files.get("resume")

    if file is None or not file.filename:
        flash("Please select a PDF or DOCX resume.")
        return render_template("index.html"), 400

    if not allowed_file(file.filename):
        flash("Unsupported file type. Please upload a PDF or DOCX file.")
        return render_template("index.html"), 400

    original_filename = secure_filename(file.filename)
    extension = original_filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid4().hex}.{extension}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)


    # Extract text
    text = ""

    try:
        if extension == "pdf":
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    text += page.extract_text() or ""
        else:
            document = Document(filepath)
            for paragraph in document.paragraphs:
                text += paragraph.text + "\n"
    except Exception:
        flash("The resume could not be read. Please upload a valid PDF or DOCX file.")
        return render_template("index.html"), 400

    if not text.strip():
        flash("No readable text was found in the resume.")
        return render_template("index.html"), 400


    # Find skills
    found_skills = extract_skills_from_text(text)

    # Resume score
    skill_score = min(len(found_skills) * 4, 40)

    education = 20 if any(
        word in text.lower()
        for word in ["education", "bachelor", "degree", "university"]
    ) else 0

    experience = 15 if any(
        word in text.lower()
        for word in ["experience", "internship"]
    ) else 0

    projects = 15 if "project" in text.lower() else 0

    certification = 10 if any(
        word in text.lower()
        for word in ["certification", "certificate"]
    ) else 0

    score = skill_score + education + experience + projects + certification


    # Job role matching
    job_matches = {}
    jd_analysis = None
    job_description = request.form.get("job_description", "").strip()
    selected_role = request.form.get("job_role", "").strip()
    ats_analysis = calculate_ats_score(text, job_description, selected_role)

    for role, required_skills in job_roles.items():
        matched = 0

        for skill in required_skills:
            if skill.lower() in [s.lower() for s in found_skills]:
                matched += 1

        percentage = int(matched / len(required_skills) * 100)
        job_matches[role] = percentage

    if job_description:
        jd_analysis = calculate_jd_match(text, job_description)
        if selected_role:
            jd_analysis["job_title"] = selected_role
    elif selected_role:
        jd_analysis = {
            "required_skills": job_roles.get(selected_role, []),
            "matched_skills": [skill for skill in job_roles.get(selected_role, []) if skill.lower() in [s.lower() for s in found_skills]],
            "missing_skills": [skill for skill in job_roles.get(selected_role, []) if skill.lower() not in [s.lower() for s in found_skills]],
            "match_percentage": int((len([skill for skill in job_roles.get(selected_role, []) if skill.lower() in [s.lower() for s in found_skills]]) / len(job_roles.get(selected_role, []))) * 100) if job_roles.get(selected_role) else 0,
            "job_title": selected_role,
        }

    saved_role = selected_role or (max(job_matches, key=job_matches.get) if job_matches else None)
    save_analysis_record(original_filename, saved_role, score, found_skills, job_matches, jd_analysis)

    return render_template(
        "result.html",
        skills=found_skills,
        score=score,
        job_matches=job_matches,
        jd_analysis=jd_analysis,
        ats_analysis=ats_analysis,
        score_breakdown={
            "Technical skills": (skill_score, 40),
            "Education": (education, 20),
            "Experience": (experience, 15),
            "Projects": (projects, 15),
            "Certifications": (certification, 10),
        },
        history=get_analysis_history(),
    )


@app.route("/history")
@login_required
def history():
    return render_template("history.html", entries=get_analysis_history())


# Automatically open browser
def open_browser():
    webbrowser.open("http://127.0.0.1:5000")


if __name__ == "__main__":

    threading.Timer(1, open_browser).start()

    app.run(debug=True)