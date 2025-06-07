import os
import datetime
import subprocess
import threading
import numpy as np
import sounddevice as sd
from pydub import AudioSegment
import json
import PySimpleGUI as sg
from utils import (
    load_all_question_sets,
    transcribe_audio_file,
    generate_docx_report
)

# Globals for recording
recorder_stream = None
recorder_chunks = []
recorder_lock = threading.Lock()

def start_recording():
    """Begin capturing microphone audio in recorder_chunks."""
    global recorder_stream, recorder_chunks
    recorder_chunks = []
    fs = 16000
    def callback(indata, frames, time, status):
        if status:
            print(status)
        with recorder_lock:
            recorder_chunks.append(indata.copy())
    recorder_stream = sd.InputStream(samplerate=fs, channels=1, callback=callback)
    recorder_stream.start()


def stop_recording_and_save(session_id, question_id):
    """Stop capture, save WAV, return path."""
    global recorder_stream, recorder_chunks
    recorder_stream.stop()
    recorder_stream.close()
    data = np.concatenate(recorder_chunks, axis=0)
    # convert to int16 PCM
    audio_int16 = (data * 32767).astype(np.int16)
    wav_folder = os.path.join(os.path.dirname(__file__), "data", "audio")
    os.makedirs(wav_folder, exist_ok=True)
    safe_session = session_id.replace(':', '').replace('-', '')
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"session_{safe_session}_q_{question_id}_{ts}.wav"
    path = os.path.join(wav_folder, fname)
    seg = AudioSegment(
        audio_int16.tobytes(),
        frame_rate=16000,
        sample_width=2,
        channels=1
    )
    seg.export(path, format='wav')
    return path


def transform_question_sets(raw_sets):
    undefined = raw_sets.get('undefined', {})
    id_map = {}
    for subcat, qlist in undefined.items():
        for q in qlist:
            id_map[q['id']] = q['text']
    final = {}
    for category, subcats in raw_sets.items():
        mapped = {}
        for subcat, items in subcats.items():
            qobjs = []
            if items and isinstance(items[0], int):
                for qid in items:
                    qobjs.append({'id': qid, 'text': id_map.get(qid, f"(missing {qid})")})
            else:
                for q in items:
                    qobjs.append(q)
            mapped[subcat] = qobjs
        final[category] = mapped
    return final

# Load and prepare question sets
raw_sets = load_all_question_sets()
question_sets = transform_question_sets(raw_sets)

temporary_session_data = {}


def session_setup_window():
    categories = list(question_sets.keys())
    layout = [
        [sg.Text("Patient Name:"), sg.Input(key="-PATIENT_NAME-")],
        [sg.Text("Patient DOB (YYYY-MM-DD):"), sg.Input(key="-PATIENT_DOB-")],
        [sg.Text("Diagnostic Category:"), sg.Combo(categories, key="-CATEGORY-", readonly=True)],
        [sg.Button("Start Session"), sg.Button("Cancel")]
    ]
    win = sg.Window("New Consultation Session", layout, modal=True)
    while True:
        event, vals = win.read()
        if event in (sg.WIN_CLOSED, "Cancel"):
            win.close()
            return None, None, None
        if event == "Start Session":
            name = vals["-PATIENT_NAME-"].strip()
            dob = vals["-PATIENT_DOB-"].strip()
            cat = vals["-CATEGORY-"]
            if not name or not dob or not cat:
                sg.popup("Please fill all fields.")
                continue
            try:
                datetime.datetime.fromisoformat(dob)
            except ValueError:
                sg.popup("DOB must be YYYY-MM-DD.")
                continue
            win.close()
            return name, dob, cat


def initialize_session(name, dob, category):
    global temporary_session_data
    started = datetime.datetime.now().isoformat(timespec="seconds")
    raw_subcats = question_sets[category]
    slots_by_subcat = {}
    for subcat, qlist in raw_subcats.items():
        slots_by_subcat[subcat] = [
            {'question_id': q['id'], 'question_text': q['text'], 'transcript': '', 'typed_answer': '', 'audio_path': ''}
            for q in qlist
        ]
    temporary_session_data = {
        'patient_name': name,
        'patient_dob': dob,
        'diagnostic_category': category,
        'started_at': started,
        'questions_by_subcat': slots_by_subcat,
        'current_subcat': list(slots_by_subcat.keys())[0],
        'current_index': 0,
        'general_notes': []
    }


def commit_current_slot(window):
    sess = temporary_session_data
    sc = sess['current_subcat']
    idx = sess['current_index']
    slot = sess['questions_by_subcat'][sc][idx]
    slot['transcript'] = window['-TRANSCRIPT-'].get().rstrip()
    slot['typed_answer'] = window['-TYPED-'].get().strip()


def get_slot():
    sess = temporary_session_data
    sc = sess['current_subcat']
    idx = sess['current_index']
    return sess['questions_by_subcat'][sc][idx]


def question_window():
    sess = temporary_session_data
    subcats = list(sess['questions_by_subcat'].keys())

    def refresh_ui(window):
        slot = get_slot()
        window['-QUESTION_TEXT-'].update(slot['question_text'])
        window['-TRANSCRIPT-'].update(slot['transcript'])
        window['-TYPED-'].update(slot['typed_answer'])
        sc = sess['current_subcat']
        sec_total = len(sess['questions_by_subcat'][sc])
        sec_idx = sess['current_index'] + 1
        window['-SECTION_PROG-'].update(f"Section: {sec_idx}/{sec_total}")
        totals = [len(lst) for lst in sess['questions_by_subcat'].values()]
        overall_total = sum(totals)
        keys = list(sess['questions_by_subcat'].keys())
        before = sum(len(sess['questions_by_subcat'][k]) for k in keys[:keys.index(sc)])
        overall_idx = before + sec_idx
        window['-TOTAL_PROG-'].update(f"Overall: {overall_idx}/{overall_total}")

    layout = [
        [sg.Text('Section:'), sg.Combo(subcats, default_value=sess['current_subcat'], key='-SUBCAT-', enable_events=True)],
        [sg.Text('', key='-SECTION_PROG-', size=(20,1)), sg.Text('', key='-TOTAL_PROG-', size=(20,1))],
        [sg.Text('', key='-QUESTION_TEXT-', size=(60,3), font=('Arial',12))],
        [sg.Button('Start Recording', key='-START_REC-'), sg.Button('Stop Recording', key='-STOP_REC-', disabled=True), sg.Button('Play Audio', key='-PLAY-')],
        [sg.Multiline('', size=(60,6), key='-TRANSCRIPT-', disabled=True)],
        [sg.Text('Typed Answer / Notes:')],
        [sg.Multiline('', size=(60,4), key='-TYPED-')],
        [sg.Button('Previous', key='-PREV-'), sg.Button('Next', key='-NEXT-')],
        [sg.Button('General Notes', key='-GENERAL_NOTES-'), sg.Button('Finish & Generate Report', key='-FINISH-')]
    ]

    window = sg.Window('Consultation Session', layout, finalize=True, resizable=True, size=(800,700))
    refresh_ui(window)

    while True:
        event, vals = window.read(timeout=100)
        if event in (sg.WIN_CLOSED,):
            if sg.popup_yes_no('Exit session? All data will be lost.') == 'Yes':
                window.close()
                return 'CANCEL'
            continue

        if event == '-SUBCAT-':
            commit_current_slot(window)
            sess['current_subcat'] = vals['-SUBCAT-']
            sess['current_index'] = 0
            refresh_ui(window)

        elif event == '-START_REC-':
            start_recording()
            window['-START_REC-'].update(disabled=True)
            window['-STOP_REC-'].update(disabled=False)

        elif event == '-STOP_REC-':
            wav = stop_recording_and_save(sess['started_at'], get_slot()['question_id'])
            get_slot()['audio_path'] = wav
            new_t = transcribe_audio_file(wav)
            existing = window['-TRANSCRIPT-'].get().rstrip()
            combined = existing + ('\n' if existing else '') + new_t
            window['-TRANSCRIPT-'].update(combined)
            window['-START_REC-'].update(disabled=False)
            window['-STOP_REC-'].update(disabled=True)

        elif event == '-PLAY-':
            path = get_slot()['audio_path']
            if path and os.path.exists(path):
                subprocess.Popen(['cmd.exe','/C', f'start "" "{path}"'])
            else:
                sg.popup('No recording found.')

        elif event == '-NEXT-':
            commit_current_slot(window)
            sc = sess['current_subcat']
            idx = sess['current_index']
            if idx < len(sess['questions_by_subcat'][sc]) - 1:
                sess['current_index'] += 1
                refresh_ui(window)
            else:
                sg.popup('Last question in this section.')

        elif event == '-PREV-':
            commit_current_slot(window)
            if sess['current_index'] > 0:
                sess['current_index'] -= 1
                refresh_ui(window)
            else:
                sg.popup('First question in this section.')

        elif event == '-GENERAL_NOTES-':
            note_layout = [
                [sg.Text('General Notes:')],
                [sg.Multiline('', size=(60,10), key='-NOTE_TEXT-')],
                [sg.Button('Save Note', key='-SAVE_NOTE-'), sg.Button('Close')]
            ]
            note_win = sg.Window('General Notes', note_layout, modal=True)
            while True:
                ne, nv = note_win.read()
                if ne in (sg.WIN_CLOSED, 'Close'):
                    note_win.close()
                    break
                if ne == '-SAVE_NOTE-':
                    txt = nv['-NOTE_TEXT-'].strip()
                    if txt:
                        ts = datetime.datetime.now().isoformat(timespec='seconds')
                        sess['general_notes'].append(f"{txt} (noted at {ts})")
                        sg.popup('Note saved.')
                    else:
                        sg.popup('Note is empty.')

        elif event == '-FINISH-':
            if sg.popup_yes_no('Finish session and generate report?') == 'Yes':
                commit_current_slot(window)
                window.close()
                return 'FINISH'

    # end while


def run_app():
    name, dob, cat = session_setup_window()
    if not name:
        return
    initialize_session(name, dob, cat)
    res = question_window()
    if res == 'CANCEL':
        sg.popup('Session cancelled.')
        return
    sess = temporary_session_data
    flat = []
    for sub in sess['questions_by_subcat'].values():
        flat.extend(sub)
    report_data = {
        'patient_name': sess['patient_name'],
        'patient_dob': sess['patient_dob'],
        'diagnostic_category': sess['diagnostic_category'],
        'started_at': sess['started_at'],
        'questions': flat,
        'general_notes': sess['general_notes']
    }
    path = generate_docx_report(report_data)
    sg.popup(f"Report generated successfully:\n{path}")

if __name__ == '__main__':
    run_app()
