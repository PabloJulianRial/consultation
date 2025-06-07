import os
import json
import datetime
import wave
from vosk import Model as VoskModel, KaldiRecognizer
from docx import Document

# Initialize Vosk model for fast offline transcription
VOSK_MODEL = VoskModel(
    os.path.join(os.path.dirname(__file__), "data", "models", "vosk-model-small-en-us-0.15")
)

def load_all_question_sets():
    """
    Reads JSON files under data/questions/ and returns a dict mapping
    diagnostic category to a dict of subcategories -> question lists.
    Supports:
      - A single combined JSON: category -> subcategory -> [ ... ]
      - Legacy per-category file with {diagnostic_category, questions: [...]}
    """
    folder = os.path.join(os.path.dirname(__file__), "data", "questions")
    question_sets = {}
    for fname in os.listdir(folder):
        if not fname.lower().endswith('.json'):
            continue
        path = os.path.join(folder, fname)
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Case A: combined file format
            if isinstance(data, dict):
                first_val = next(iter(data.values()), None)
                if isinstance(first_val, dict):
                    for category, subs in data.items():
                        question_sets[category] = subs
                    continue
            # Case B: legacy per-file format
            category = data.get('diagnostic_category')
            questions = data.get('questions', [])
            if category:
                question_sets[category] = {"All": questions}
    return question_sets


def transcribe_audio_file(wav_path):
    """
    Fast offline transcription using Vosk.
    Returns the recognized transcript string.
    """
    wf = wave.open(wav_path, 'rb')
    rec = KaldiRecognizer(VOSK_MODEL, wf.getframerate())
    rec.SetWords(False)

    transcript = ''
    while True:
        data = wf.readframes(4000)
        if len(data) == 0:
            break
        if rec.AcceptWaveform(data):
            res = json.loads(rec.Result())
            transcript += ' ' + res.get('text', '')
    final_res = json.loads(rec.FinalResult())
    transcript += ' ' + final_res.get('text', '')
    transcript = transcript.strip()
    print(f"Transcript: {transcript}\n")
    return transcript


def generate_docx_report(session_data):
    """
    Given session_data dict, writes a polished DOCX under reports/ and returns its path.
    session_data fields:
      - patient_name, patient_dob, diagnostic_category, started_at
      - questions: flat list of {question_id, question_text, transcript, typed_answer}
      - general_notes: list of strings
    """
    reports_folder = os.path.join(os.path.dirname(__file__), 'reports')
    os.makedirs(reports_folder, exist_ok=True)

    safe_name = session_data['patient_name'].replace(' ', '_')
    timestamp = session_data['started_at'].replace(':', '').replace('-', '')
    filename = f"{safe_name}_{timestamp}.docx"
    full_path = os.path.join(reports_folder, filename)

    template_path = os.path.join(
        os.path.dirname(__file__), 'templates', 'report_template.docx'
    )
    if os.path.exists(template_path):
        doc = Document(template_path)
    else:
        doc = Document()

    # Header info
    doc.add_heading('Consultation Report', level=0)
    doc.add_paragraph(f"Patient Name: {session_data['patient_name']}")
    doc.add_paragraph(f"Date of Birth: {session_data['patient_dob']}")
    doc.add_paragraph(f"Diagnostic Category: {session_data['diagnostic_category']}")
    doc.add_paragraph(f"Session Started: {session_data['started_at']}")
    doc.add_paragraph('')

    # Q&A
    doc.add_heading('Questions & Answers', level=1)
    for item in session_data['questions']:
        qtext = item.get('question_text', '')
        transcript = item.get('transcript', '') or '(no recording/transcript)'
        typed = item.get('typed_answer', '') or '(no typed notes)'

        doc.add_heading(f"Q: {qtext}", level=2)
        doc.add_paragraph('Transcript:')
        doc.add_paragraph(transcript, style='Intense Quote')
        doc.add_paragraph('Typed Notes/Corrections:')
        doc.add_paragraph(typed)
        doc.add_paragraph('')

    # General notes
    doc.add_heading('General Notes', level=1)
    if session_data.get('general_notes'):
        for note in session_data['general_notes']:
            doc.add_paragraph(str(note))
    else:
        doc.add_paragraph('(none)')

    doc.save(full_path)
    print(f"Report saved to: {full_path}")
    return full_path