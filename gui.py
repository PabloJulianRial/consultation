# gui.py

import PySimpleGUI as sg
import datetime
import os
import subprocess

from utils import (
    load_all_question_sets,
    record_audio_for_question,
    transcribe_audio_file,
    generate_docx_report
)

# In-memory session state
question_sets = {}           # category → list of {id, text}
current_session_data = {}    # holds patient info, questions slots, notes
current_question_index = 0   # pointer into current_session_data["questions"]


def session_setup_window():
    categories = list(question_sets.keys())
    layout = [
        [sg.Text("Patient Name:"), sg.Input(key="-PATIENT_NAME-")],
        [sg.Text("Patient DOB (YYYY-MM-DD):"), sg.Input(key="-PATIENT_DOB-")],
        [sg.Text("Category:"), sg.Combo(categories, key="-CATEGORY-", readonly=True)],
        [sg.Button("Start Session"), sg.Button("Cancel")]
    ]
    window = sg.Window("New Consultation Session", layout, modal=True)
    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, "Cancel"):
            window.close()
            return None, None, None
        if event == "Start Session":
            name = values["-PATIENT_NAME-"].strip()
            dob = values["-PATIENT_DOB-"].strip()
            cat = values["-CATEGORY-"]
            if not name or not dob or not cat:
                sg.popup("Please fill in all fields.")
                continue
            try:
                datetime.datetime.fromisoformat(dob)
            except ValueError:
                sg.popup("DOB must be YYYY-MM-DD.")
                continue
            window.close()
            return name, dob, cat


def initialize_session(patient_name, patient_dob, diagnostic_category):
    global current_session_data, current_question_index
    started_at = datetime.datetime.now().isoformat(timespec="seconds")
    slots = []
    for q in question_sets[diagnostic_category]:
        slots.append({
            "question_id": q["id"],
            "question_text": q["text"],
            "transcript": "",
            "typed_answer": "",
            "audio_path": ""
        })
    current_session_data = {
        "patient_name": patient_name,
        "patient_dob": patient_dob,
        "diagnostic_category": diagnostic_category,
        "started_at": started_at,
        "questions": slots,
        "general_notes": []
    }
    current_question_index = 0


def commit_current_slot(window):
    """Save current transcript and typed-answer UI fields into the slot."""
    slot = current_session_data["questions"][current_question_index]
    transcript_text = window["-TRANSCRIPT-"].get().rstrip()
    typed_text = window["-TYPED-"].get().strip()
    slot["transcript"] = transcript_text
    slot["typed_answer"] = typed_text


def question_window():
    global current_question_index, current_session_data
    def get_slot():
        return current_session_data["questions"][current_question_index]

    total = len(current_session_data["questions"])
    slot = get_slot()

    layout = [
        [sg.Text(f"Question {current_question_index+1}/{total}", font=("Arial", 14))],
        [sg.Text(slot["question_text"], key="-QUESTION_TEXT-", size=(60,3), font=("Arial",12))],
        [sg.Button("Record Answer", key="-RECORD-"), sg.Button("Play Audio", key="-PLAY-")],
        [sg.Multiline(slot["transcript"], size=(60,6), key="-TRANSCRIPT-", disabled=True)],
        [sg.Text("Typed Answer / Notes:")],
        [sg.Multiline(slot["typed_answer"], size=(60,4), key="-TYPED-")],
        [sg.Button("Previous", key="-PREV-"), sg.Button("Next", key="-NEXT-")],
        [sg.Button("General Notes", key="-GENERAL_NOTES-"), sg.Button("Finish & Generate Report", key="-FINISH-")]
    ]

    window = sg.Window("Consultation Session", layout, finalize=True, resizable=True, size=(800,700))

    while True:
        event, values = window.read(timeout=100)
        if event in (sg.WIN_CLOSED,):
            if sg.popup_yes_no("Exit session? All data will be lost.") == "Yes":
                window.close()
                return "CANCEL"
            else:
                continue

        if event == "-RECORD-":
            session_id = current_session_data["started_at"]
            qid = slot["question_id"]
            wav_path = record_audio_for_question(session_id, qid)
            new_transcript = transcribe_audio_file(wav_path)
            existing = window["-TRANSCRIPT-"].get().rstrip()
            combined = existing + ("\n" if existing else "") + new_transcript
            window["-TRANSCRIPT-"].update(combined)
            # leave slot update to navigation

        elif event == "-PLAY-":
            audio_path = slot["audio_path"]
            if audio_path and os.path.exists(audio_path):
                subprocess.Popen(["cmd.exe", "/C", f"start \"\" \"{audio_path}\""])
            else:
                sg.popup("No recording yet.")

        elif event == "-NEXT-":
            commit_current_slot(window)
            if current_question_index < total - 1:
                current_question_index += 1
                slot = get_slot()
                window["-QUESTION_TEXT-"].update(slot["question_text"])
                window["-TRANSCRIPT-"].update(slot["transcript"])
                window["-TYPED-"].update(slot["typed_answer"])
            else:
                sg.popup("This is the last question.")

        elif event == "-PREV-":
            commit_current_slot(window)
            if current_question_index > 0:
                current_question_index -= 1
                slot = get_slot()
                window["-QUESTION_TEXT-"].update(slot["question_text"])
                window["-TRANSCRIPT-"].update(slot["transcript"])
                window["-TYPED-"].update(slot["typed_answer"])
            else:
                sg.popup("This is the first question.")

        elif event == "-GENERAL_NOTES-":
            note_layout = [
                [sg.Text("General Notes:")],
                [sg.Multiline("", size=(60,10), key="-NOTE_TEXT-")],
                [sg.Button("Save Note", key="-SAVE_NOTE-"), sg.Button("Close")]
            ]
            note_win = sg.Window("General Notes", note_layout, modal=True)
            while True:
                nevent, nvals = note_win.read()
                if nevent in (sg.WIN_CLOSED, "Close"):
                    note_win.close()
                    break
                if nevent == "-SAVE_NOTE-":
                    text = nvals["-NOTE_TEXT-"].strip()
                    if text:
                        now = datetime.datetime.now().isoformat(timespec="seconds")
                        current_session_data["general_notes"].append(f"{text} (noted at {now})")
                        sg.popup("Note saved.")
                    else:
                        sg.popup("Note is empty.")

        elif event == "-FINISH-":
            if sg.popup_yes_no("Finish and generate report?") == "Yes":
                commit_current_slot(window)
                window.close()
                return "FINISH"

    # end while


def run_app():
    global question_sets
    question_sets = load_all_question_sets()
    name, dob, category = session_setup_window()
    if not name:
        return
    initialize_session(name, dob, category)
    result = question_window()
    if result == "CANCEL":
        sg.popup("Session cancelled.")
        return
    save_path = generate_docx_report(current_session_data)
    sg.popup(f"Report generated:\n{save_path}")


if __name__ == "__main__":
    run_app()
