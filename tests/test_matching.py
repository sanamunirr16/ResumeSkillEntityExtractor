from app import extract_skills_from_text, calculate_jd_match


def test_extract_skills_from_text():
    text = "Experienced in Python, SQL, Flask, React, and Git."
    skills = extract_skills_from_text(text)

    assert "Python" in skills
    assert "SQL" in skills
    assert "Flask" in skills
    assert "React" in skills
    assert "Git" in skills


def test_calculate_jd_match():
    resume_text = "Strong Python, SQL, and Flask experience. Git used in projects."
    jd_text = "Seeking Python, SQL, Flask, Git, and HTML developer."

    result = calculate_jd_match(resume_text, jd_text)

    assert result["match_percentage"] == 80
    assert result["matched_skills"] == ["Python", "SQL", "Flask", "Git"]
    assert result["missing_skills"] == ["HTML"]
