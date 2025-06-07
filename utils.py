import os
import json
import datetime
import sounddevice as sd
import numpy as np
from pydub import AudioSegment
import wave
from vosk import Model as VoskModel, KaldiRecognizer
from docx import Document

# Load Vosk model for fast offline transcription
VOSK_MODEL = VoskModel(
    os.path.join(os.path.dirname(__file__), "data", "models", "vosk-model-small-en-us-0.15")
)


def load_all_question_sets():
    """
    Reads JSON files under data/questions/ and returns a dict
    mapping diagnostic category to a list of question dicts ({id, text}).
    """
    folder = os.path.join(os.path.dirname(__file__), "data", "questions")
    question_sets = {}
    for fname in os.listdir(folder):
        if not fname.lower().endswith('.json'):
            continue
        path = os.path.join(folder, fname)
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # If file defines multiple categories
            if isinstance(data, dict) and all(isinstance(v, list) for v in data.values()):
                for category, qlist in data.items():
                    question_sets[category] = qlist
            else:
                # Single category file format
                category = data.get('diagnostic_category')
                questions = data.get('questions', [])
                if category:
                    question_sets[category] = questions
    return question_sets


def record_audio_for_question(session_id, question_id):
    """
    Records audio from the default microphone until Enter is pressed twice.
    Saves a WAV under data/audio/ and returns its full path.
    """
    print(f"\n--- Recording for Session {session_id}, Question {question_id} ---")
    print("Press Enter to start recording. Press Enter again to stop.")
    input("Ready? Hit Enter to begin…")
    fs = 16000  # sample rate
    audio_chunks = []

    def callback(indata, frames, time, status):
        if status:
            print(status, flush=True)
        audio_chunks.append(indata.copy())

    with sd.InputStream(samplerate=fs, channels=1, callback=callback):
        print("Recording… Press Enter to stop.")
        input()

    audio_data = np.concatenate(audio_chunks, axis=0)

    # Convert to int16 PCM for WAV
    audio_int16 = (audio_data * 32767).astype(np.int16)
    wav_folder = os.path.join(os.path.dirname(__file__), "data", "audio")
    os.makedirs(wav_folder, exist_ok=True)

    # Sanitize session_id for filename
    safe_session = session_id.replace(':', '').replace('-', '')
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    wav_filename = f"session_{safe_session}_q_{question_id}_{timestamp}.wav"
    wav_path = os.path.join(wav_folder, wav_filename)

    seg = AudioSegment(
        audio_int16.tobytes(),
        frame_rate=fs,
        sample_width=2,
        channels=1
    )
    seg.export(wav_path, format='wav')
    print(f"Saved recording to: {wav_path}")
    return wav_path


def transcribe_audio_file(wav_path):
    """
    Fast offline transcription using Vosk.
    Returns the recognized text.
    """
    wf = wave.open(wav_path, 'rb')
    rec = KaldiRecognizer(VOSK_MODEL, wf.getframerate())
    rec.SetWords(False)

    result_text = ''
    while True:
        data = wf.readframes(4000)
        if len(data) == 0:
            break
        if rec.AcceptWaveform(data):
            res = json.loads(rec.Result())
            result_text += ' ' + res.get('text', '')
    final_res = json.loads(rec.FinalResult())
    result_text += ' ' + final_res.get('text', '')
    transcript = result_text.strip()
    print(f"Transcript: {transcript}\n")
    return transcript


def generate_docx_report(session_data):
    """
    Given in-memory session_data, create a DOCX report in 'reports/' and
    return its path.
    session_data keys: patient_name, patient_dob, diagnostic_category,
      started_at, questions (list of dicts), general_notes (list).
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

    doc.add_heading('Consultation Report', level=0)
    doc.add_paragraph(f"Patient Name: {session_data['patient_name']}")
    doc.add_paragraph(f"Date of Birth: {session_data['patient_dob']}")
    doc.add_paragraph(f"Diagnostic Category: {session_data['diagnostic_category']}")
    doc.add_paragraph(f"Session Started: {session_data['started_at']}")
    doc.add_paragraph('')

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

    doc.add_heading('General Notes', level=1)
    if session_data.get('general_notes'):
        for note in session_data['general_notes']:
            doc.add_paragraph(str(note))
    else:
        doc.add_paragraph('(none)')

    doc.save(full_path)
    print(f"Report saved to: {full_path}")
    return full_path
