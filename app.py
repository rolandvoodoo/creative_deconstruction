import os, json, time, base64, subprocess, tempfile
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import google.generativeai as genai

app = Flask(__name__)
CORS(app)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "./reports"))
OUTPUT_DIR.mkdir(exist_ok=True)

GEMINI_PROMPT = '''You are an expert mobile game UA analyst for Voodoo. Analyze this video ad with extreme precision - every second and every sound matters.

Return ONLY valid JSON (no markdown, no commentary outside the JSON):

{
  "total_duration_seconds": <number>,
  "ad_type": "<progression|near-win|near-loss|challenge|fail|ASMR|UGC|tutorial>",

  "transcript": {
    "has_voiceover": <true|false>,
    "has_dialogue": <true|false>,
    "has_text_overlays": <true|false>,
    "spoken_lines": [{"timestamp": "<MM:SS>", "speaker": "<voiceover|character|ugc_creator>", "text": "<exact words>"}],
    "text_overlays": [{"timestamp": "<MM:SS>", "text": "<exact text on screen>", "purpose": "<hook|instruction|cta|score|other>"}],
    "language": "<language or none>",
    "transcript_notes": "<overall note on how copy/text contributes or hurts the ad>"
  },

  "shot_by_shot": [
    {
      "start": "<MM:SS>",
      "end": "<MM:SS>",
      "visual": "<precise description of what is shown>",
      "audio": "<music description + every SFX you hear>",
      "pacing": "<cut/transition type>"
    }
  ],

  "hook": {
    "first_frame_description": "<what appears in the very first frame>",
    "has_logo_or_titlecard": <true|false>,
    "dead_seconds_before_action": <number>,
    "scroll_stopper_element": "<the specific visual or audio element that grabs attention>",
    "verdict": "<one highly specific sentence>"
  },

  "gameplay_clarity": {
    "mechanic_legible_in_3s": <true|false>,
    "objective_clear": <true|false>,
    "ui_cluttered": <true|false>,
    "first_clear_gameplay_timestamp": "<MM:SS>",
    "verdict": "<one highly specific sentence>"
  },

  "scenario_intention": {
    "has_designed_arc": <true|false>,
    "arc_type": "<tension_to_payoff|loop|linear_progression|none>",
    "arc_description": "<describe the emotional journey>",
    "payoff_moment_timestamp": "<MM:SS or null>",
    "verdict": "<one highly specific sentence>"
  },

  "juiciness_visual_polish": {
    "particles": <true|false>,
    "physics_reactions": <true|false>,
    "screen_shake": <true|false>,
    "camera_work": "<static|dynamic|both>",
    "animation_quality": "<weak|average|strong>",
    "best_juicy_moment_timestamp": "<MM:SS>",
    "best_juicy_moment_description": "<what exactly happens>",
    "verdict": "<one highly specific sentence>"
  },

  "sound_design": {
    "music_genre": "<e.g. upbeat electronic, dramatic orchestral>",
    "music_energy": "<low|medium|high>",
    "music_matches_gameplay_tone": <true|false>,
    "sfx_reactive_to_actions": <true|false>,
    "notable_sfx": [{"timestamp": "<MM:SS>", "sound": "<description>", "effective": <true|false>}],
    "silence_or_dead_audio_moments": ["<timestamp range if any>"],
    "verdict": "<one highly specific sentence>"
  },

  "ui_visual_clarity": {
    "hud_clutter_level": "<none|light|heavy>",
    "color_contrast_guides_eye": <true|false>,
    "worst_clutter_timestamp": "<MM:SS or null>",
    "verdict": "<one highly specific sentence>"
  },

  "emotion_narrative_arc": {
    "primary_emotion": "<frustration|satisfaction|curiosity|urgency|awe|humor|tension>",
    "emotional_arc": "<flat|builds|peaks_and_drops|consistent_high>",
    "strongest_emotional_moment_timestamp": "<MM:SS or null>",
    "verdict": "<one highly specific sentence>"
  },

  "end_card": {
    "present": <true|false>,
    "timestamp": "<MM:SS or null>",
    "cta_text": "<exact text if visible>",
    "compelling": <true|false>,
    "verdict": "<one highly specific sentence>"
  },

  "key_timestamps": {
    "hook": "<MM:SS>",
    "first_clear_gameplay": "<MM:SS>",
    "best_juicy_moment": "<MM:SS>",
    "payoff_or_climax": "<MM:SS or null>",
    "weakest_moment": "<MM:SS>",
    "end_card_or_final_frame": "<MM:SS>"
  },

  "overall_strengths": ["<specific timestamped strength>"],
  "overall_weaknesses": ["<specific timestamped weakness>"],

  "video_script": "<continuous second-by-second narration of the entire ad>"
}'''


def ts_to_seconds(ts):
    if not ts or ts == "null":
        return None
    try:
        parts = ts.strip().split(":")
        return int(parts[0]) * 60 + float(parts[1])
    except:
        return None


def extract_frame(video_path, timestamp_str, output_path):
    secs = ts_to_seconds(timestamp_str)
    if secs is None:
        return False
    cmd = ["ffmpeg", "-y", "-ss", str(secs), "-i", video_path,
           "-frames:v", "1", "-q:v", "2", output_path]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and os.path.exists(output_path)


def b64img(path):
    with open(path, "rb") as f:
        return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()


def grade_from_gemini(key, data):
    verdicts_bad = ["weak", "missing", "absent", "cluttered", "dead", "none", "poor", "flat", "fails"]
    verdicts_good = ["strong", "excellent", "clear", "sharp", "compelling", "effective", "reactive"]
    verdict = (data.get("verdict") or "").lower()
    if any(w in verdict for w in verdicts_bad):
        return "BAD"
    if any(w in verdict for w in verdicts_good):
        return "GOOD"
    return "OK"


def grade_color(grade):
    return {"GOOD": "#166534", "OK": "#92400e", "BAD": "#991b1b"}.get(grade, "#555")


def build_html(ad_name, gemini, frames_b64, analyzed_date):
    def g(key): return gemini.get(key, {})
    def grade(key): return grade_from_gemini(key, g(key))

    dims = [
        ("Hook", grade("hook"), g("hook").get("verdict", "")),
        ("Gameplay Clarity", grade("gameplay_clarity"), g("gameplay_clarity").get("verdict", "")),
        ("Scenario / Intention", grade("scenario_intention"), g("scenario_intention").get("verdict", "")),
        ("Juiciness & Polish", grade("juiciness_visual_polish"), g("juiciness_visual_polish").get("verdict", "")),
        ("Sound Design", grade("sound_design"), g("sound_design").get("verdict", "")),
        ("UI & Visual Clarity", grade("ui_visual_clarity"), g("ui_visual_clarity").get("verdict", "")),
        ("Transcript & Copy", "N/A" if not g("transcript").get("has_voiceover") and not g("transcript").get("has_text_overlays") else grade("transcript"), g("transcript").get("transcript_notes", "No spoken/written content")),
        ("Emotion & Arc", grade("emotion_narrative_arc"), g("emotion_narrative_arc").get("verdict", "")),
        ("End Card / CTA", grade("end_card"), g("end_card").get("verdict", "")),
    ]

    rows = ""
    for name, gr, verdict in dims:
        c = grade_color(gr)
        rows += f'<tr><td>{name}</td><td class="grade" style="color:{c}">{gr}</td><td>{verdict}</td></tr>\n'

    timeline = ""
    for shot in gemini.get("shot_by_shot", []):
        timeline += f'<div><span class="ts">{shot.get("start","")}-{shot.get("end","")}</span> {shot.get("visual","")} &nbsp;<span class="audio-note">♪ {shot.get("audio","")}</span></div>\n'

    def frame_fig(key, label):
        b64 = frames_b64.get(key)
        if not b64:
            return ""
        return f'<figure class="screenshot"><img src="{b64}" alt="{label}"><figcaption>{label}</figcaption></figure>'

    def sfx_rows():
        out = ""
        for sfx in g("sound_design").get("notable_sfx", []):
            eff = "✓" if sfx.get("effective") else "✗"
            out += f'<li><span class="ts">{sfx.get("timestamp","")}</span> {sfx.get("sound","")} — {eff}</li>\n'
        return out

    def transcript_rows():
        out = ""
        for line in g("transcript").get("spoken_lines", []):
            out += f'<li><span class="ts">{line.get("timestamp","")}</span> "{line.get("text","")}" — {line.get("speaker","")}</li>\n'
        return out or "<li>None detected</li>"

    strengths = "".join(f"<li>{s}</li>" for s in gemini.get("overall_strengths", []))
    weaknesses = "".join(f"<li>{w}</li>" for w in gemini.get("overall_weaknesses", []))
    dur = gemini.get("total_duration_seconds", "?")
    ad_type = gemini.get("ad_type", "?")

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>Creative Deconstruction: {ad_name}</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; max-width: 920px; margin: 40px auto; padding: 0 24px; color: #1a1a1a; }}
    h1 {{ font-size: 22px; margin-bottom: 4px; }}
    .meta {{ color: #666; font-size: 14px; margin-bottom: 32px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 24px 0; }}
    th {{ background: #f5f5f5; text-align: left; padding: 10px 12px; font-size: 13px; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #eee; font-size: 14px; vertical-align: top; }}
    .grade {{ font-weight: 600; white-space: nowrap; }}
    .section {{ margin: 32px 0; }}
    .section h2 {{ font-size: 17px; margin-bottom: 12px; border-bottom: 2px solid #eee; padding-bottom: 8px; }}
    .frames-row {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0; }}
    figure.screenshot {{ margin: 0; }}
    figure.screenshot img {{ max-width: 220px; border-radius: 8px; border: 1px solid #ddd; display: block; }}
    figure.screenshot figcaption {{ font-size: 11px; color: #888; margin-top: 4px; }}
    ul {{ margin: 8px 0; padding-left: 20px; }}
    li {{ margin: 5px 0; font-size: 14px; line-height: 1.5; }}
    .works {{ color: #166534; }} .weak {{ color: #991b1b; }}
    .timeline {{ background: #f9f9f9; border-left: 3px solid #ccc; padding: 12px 16px; margin: 16px 0; font-size: 13px; line-height: 1.8; }}
    .ts {{ font-weight: 600; color: #444; display: inline-block; min-width: 70px; }}
    .audio-note {{ color: #1d4ed8; font-style: italic; }}
    .sound-box {{ background: #eff6ff; border-radius: 8px; padding: 14px 18px; margin: 12px 0; font-size: 14px; }}
    .sound-box strong {{ display: inline-block; min-width: 130px; }}
    .script-box {{ background: #fafafa; border: 1px solid #eee; border-radius: 8px; padding: 16px; font-size: 13px; line-height: 1.8; white-space: pre-wrap; }}
  </style>
</head>
<body>
<h1>Creative Deconstruction: {ad_name}</h1>
<div class="meta"><strong>Format:</strong> Video &nbsp;|&nbsp; <strong>Type:</strong> {ad_type} &nbsp;|&nbsp; <strong>Duration:</strong> {dur}s &nbsp;|&nbsp; <strong>Analyzed:</strong> {analyzed_date}</div>
<table>
  <tr><th>Dimension</th><th>Grade</th><th>One-liner verdict</th></tr>
  {rows}
</table>
<div class="section"><h2>Shot-by-Shot Timeline</h2><div class="timeline">{timeline}</div></div>
<div class="section"><h2>Hook — {grade("hook")}</h2>
  <div class="frames-row">{frame_fig("hook", g("key_timestamps").get("hook","") + " — Hook")}</div>
  <ul>
    <li>First frame: {g("hook").get("first_frame_description","")}</li>
    <li>Dead seconds before action: {g("hook").get("dead_seconds_before_action","?")}</li>
    <li>Scroll-stopper: {g("hook").get("scroll_stopper_element","")}</li>
    <li>Logo/titlecard: {g("hook").get("has_logo_or_titlecard","?")}</li>
  </ul>
</div>
<div class="section"><h2>Gameplay Clarity — {grade("gameplay_clarity")}</h2>
  <div class="frames-row">{frame_fig("gameplay", g("key_timestamps").get("first_clear_gameplay","") + " — First gameplay")}</div>
  <ul>
    <li>Mechanic legible in 3s: {g("gameplay_clarity").get("mechanic_legible_in_3s","?")}</li>
    <li>Objective clear: {g("gameplay_clarity").get("objective_clear","?")}</li>
    <li>UI cluttered: {g("gameplay_clarity").get("ui_cluttered","?")}</li>
    <li>First gameplay at: {g("gameplay_clarity").get("first_clear_gameplay_timestamp","?")}</li>
  </ul>
</div>
<div class="section"><h2>Scenario / Intention — {grade("scenario_intention")}</h2>
  <div class="frames-row">
    {frame_fig("payoff", g("key_timestamps").get("payoff_or_climax","") + " — Payoff")}
    {frame_fig("weakest", g("key_timestamps").get("weakest_moment","") + " — Weakest")}
  </div>
  <ul>
    <li>Designed arc: {g("scenario_intention").get("has_designed_arc","?")}</li>
    <li>Arc type: {g("scenario_intention").get("arc_type","?")}</li>
    <li>{g("scenario_intention").get("arc_description","")}</li>
  </ul>
</div>
<div class="section"><h2>Juiciness & Visual Polish — {grade("juiciness_visual_polish")}</h2>
  <div class="frames-row">{frame_fig("juicy", g("key_timestamps").get("best_juicy_moment","") + " — Best juicy moment")}</div>
  <ul>
    <li>Particles: {g("juiciness_visual_polish").get("particles","?")} | Physics: {g("juiciness_visual_polish").get("physics_reactions","?")} | Screen shake: {g("juiciness_visual_polish").get("screen_shake","?")}</li>
    <li>Camera: {g("juiciness_visual_polish").get("camera_work","?")} | Animation: {g("juiciness_visual_polish").get("animation_quality","?")}</li>
    <li>Best moment ({g("juiciness_visual_polish").get("best_juicy_moment_timestamp","?")}): {g("juiciness_visual_polish").get("best_juicy_moment_description","")}</li>
  </ul>
</div>
<div class="section"><h2>Sound Design — {grade("sound_design")}</h2>
  <div class="sound-box">
    <div><strong>Music:</strong> {g("sound_design").get("music_genre","")} — {g("sound_design").get("music_energy","")} energy</div>
    <div><strong>SFX reactive:</strong> {g("sound_design").get("sfx_reactive_to_actions","?")}</div>
    <ul>{sfx_rows()}</ul>
  </div>
</div>
<div class="section"><h2>UI & Visual Clarity — {grade("ui_visual_clarity")}</h2>
  <ul>
    <li>HUD clutter: {g("ui_visual_clarity").get("hud_clutter_level","?")}</li>
    <li>Color contrast guides eye: {g("ui_visual_clarity").get("color_contrast_guides_eye","?")}</li>
    <li>Worst clutter at: {g("ui_visual_clarity").get("worst_clutter_timestamp","none")}</li>
  </ul>
</div>
<div class="section"><h2>Transcript & Copy</h2>
  <div class="sound-box">
    <div><strong>Voiceover:</strong> {g("transcript").get("has_voiceover","?")}</div>
    <div><strong>Text overlays:</strong> {g("transcript").get("has_text_overlays","?")}</div>
    <ul>{transcript_rows()}</ul>
  </div>
  <ul><li>{g("transcript").get("transcript_notes","")}</li></ul>
</div>
<div class="section"><h2>Emotion & Narrative Arc — {grade("emotion_narrative_arc")}</h2>
  <ul>
    <li>Primary emotion: {g("emotion_narrative_arc").get("primary_emotion","?")}</li>
    <li>Arc: {g("emotion_narrative_arc").get("emotional_arc","?")}</li>
    <li>Strongest moment at: {g("emotion_narrative_arc").get("strongest_emotional_moment_timestamp","?")}</li>
  </ul>
</div>
<div class="section"><h2>End Card / CTA — {grade("end_card")}</h2>
  <div class="frames-row">{frame_fig("endcard", g("key_timestamps").get("end_card_or_final_frame","") + " — End card")}</div>
  <ul>
    <li>Present: {g("end_card").get("present","?")} | Compelling: {g("end_card").get("compelling","?")}</li>
    <li>CTA text: "{g("end_card").get("cta_text","none")}"</li>
  </ul>
</div>
<div class="section"><h2>Strengths & Weaknesses</h2>
  <ul class="works">{strengths}</ul>
  <ul class="weak">{weaknesses}</ul>
</div>
<div class="section"><h2>Video Script</h2>
  <div class="script-box">{gemini.get("video_script","")}</div>
</div>
</body>
</html>"""


@app.route("/analyze", methods=["POST"])
def analyze():
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not set"}), 500
    if "video" not in request.files:
        return jsonify({"error": "No video file"}), 400

    video_file = request.files["video"]
    ad_name = Path(video_file.filename).stem

    with tempfile.TemporaryDirectory() as tmp:
        video_path = os.path.join(tmp, video_file.filename)
        video_file.save(video_path)

        genai.configure(api_key=GEMINI_API_KEY)
        print(f"Uploading to Gemini: {video_path}")
        gfile = genai.upload_file(path=video_path)

        while gfile.state.name == "PROCESSING":
            time.sleep(3)
            gfile = genai.get_file(gfile.name)

        if gfile.state.name == "FAILED":
            return jsonify({"error": "Gemini video processing failed"}), 500

        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content([gfile, GEMINI_PROMPT], generation_config={"temperature": 0.1})
        genai.delete_file(gfile.name)

        raw = response.text
        start = raw.find("{")
        end = raw.rfind("}") + 1
        gemini_data = json.loads(raw[start:end])

        kts = gemini_data.get("key_timestamps", {})
        frames_dir = os.path.join(tmp, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        frame_map = {
            "hook":     kts.get("hook"),
            "gameplay": kts.get("first_clear_gameplay"),
            "juicy":    kts.get("best_juicy_moment"),
            "payoff":   kts.get("payoff_or_climax"),
            "weakest":  kts.get("weakest_moment"),
            "endcard":  kts.get("end_card_or_final_frame"),
        }

        frames_b64 = {}
        for key, ts in frame_map.items():
            if ts and ts != "null":
                out = os.path.join(frames_dir, f"{key}.jpg")
                if extract_frame(video_path, ts, out):
                    frames_b64[key] = b64img(out)

        analyzed_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        html = build_html(ad_name, gemini_data, frames_b64, analyzed_date)

        report_path = OUTPUT_DIR / f"{ad_name}_deconstruction.html"
        report_path.write_text(html, encoding="utf-8")

        dims_summary = []
        for name, key in [
            ("Hook", "hook"), ("Gameplay Clarity", "gameplay_clarity"),
            ("Scenario / Intention", "scenario_intention"),
            ("Juiciness & Polish", "juiciness_visual_polish"),
            ("Sound Design", "sound_design"), ("UI & Visual Clarity", "ui_visual_clarity"),
            ("Emotion & Arc", "emotion_narrative_arc"), ("End Card / CTA", "end_card"),
        ]:
            sec = gemini_data.get(key, {})
            dims_summary.append({
                "name": name,
                "grade": grade_from_gemini(key, sec),
                "verdict": sec.get("verdict", "")
            })

        return jsonify({
            "ad_name": ad_name,
            "ad_type": gemini_data.get("ad_type"),
            "duration": gemini_data.get("total_duration_seconds"),
            "dimensions": dims_summary,
            "strengths": gemini_data.get("overall_strengths", []),
            "weaknesses": gemini_data.get("overall_weaknesses", []),
            "report_filename": f"{ad_name}_deconstruction.html",
        })


@app.route("/report/<filename>")
def get_report(filename):
    path = OUTPUT_DIR / filename
    if not path.exists():
        return "Not found", 404
    return send_file(path, mimetype="text/html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"Starting on port {port}")
    app.run(host="0.0.0.0", port=port)
