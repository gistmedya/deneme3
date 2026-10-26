#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import time
import os
import re
import json
import requests
import threading
from collections import deque

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "digimax2"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

M3U_URL = os.getenv("M3U_URL") or "https://raw.githubusercontent.com/gistmedya/0101/refs/heads/main/deneme.m3u"
LOGO_URL = os.getenv("LOGO_URL") or "https://raw.githubusercontent.com/gistmedya/0101/refs/heads/main/1791047356035.png"

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "digi.json")
GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
STREAM_REFERER = "https://vidmody.com/"

LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "1.0"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

DECODER_THREADS = os.getenv("DECODER_THREADS", "2")
WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "45"))
LINK_CHANGE_REWIND_SECONDS = int(os.getenv("LINK_CHANGE_REWIND_SECONDS", "15"))


def format_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def get_highest_quality_hls_link(url):
    if not url.endswith('.m3u8') and '.m3u8?' not in url:
        return url

    try:
        headers = {'User-Agent': STREAM_USER_AGENT, 'Referer': STREAM_REFERER}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200 and '#EXTM3U' in res.text:
            lines = res.text.splitlines()
            best_url = None
            max_bandwidth = 0
            base_url = url.rsplit('/', 1)[0] + '/'

            for i, line in enumerate(lines):
                if line.startswith('#EXT-X-STREAM-INF'):
                    bw_match = re.search(r'BANDWIDTH=(\d+)', line)
                    bw = int(bw_match.group(1)) if bw_match else 0

                    if i + 1 < len(lines):
                        sub_url = lines[i + 1].strip()
                        if not sub_url.startswith('http'):
                            sub_url = base_url + sub_url

                        if bw > max_bandwidth:
                            max_bandwidth = bw
                            best_url = sub_url

            if best_url:
                return best_url
    except Exception as e:
        print(f"⚠️ HLS Kalite ayrıştırma hatası: {e}")

    return url


def get_video_duration_ffprobe(video_url):
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-allowed_extensions', 'ALL',
        '-analyzeduration', '10000000',
        '-probesize', '10000000',
        '-select_streams', 'v:0',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        '-headers', f"User-Agent: {STREAM_USER_AGENT}\r\nReferer: {STREAM_REFERER}\r\n",
        video_url
    ]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=8).decode('utf-8').strip()
        duration = float(output)
        if duration > 0:
            return duration
    except Exception as e:
        print(f"⚠️ ffprobe ile süre okunamadı: {e}")
    return 0.0


def get_local_state():
    if os.path.exists(STATE_FILE_NAME):
        if os.path.getsize(STATE_FILE_NAME) == 0:
            return 0, 0, "", ""
        try:
            with open(STATE_FILE_NAME, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("last_index", 0), data.get("last_seconds", 0), data.get("last_url", ""), data.get("last_title", "")
        except Exception as e:
            print(f"⚠️ Yerel state okuma hatası: {e}")
    return 0, 0, "", ""


def update_local_state(index, seconds, url="", title=""):
    try:
        data = {
            "last_index": int(index),
            "last_seconds": int(seconds),
            "last_url": url,
            "last_title": title,
        }
        with open(STATE_FILE_NAME, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Yerel state yazma hatası: {e}")


def get_m3u_playlist(m3u_url):
    try:
        headers = {'User-Agent': STREAM_USER_AGENT, 'Referer': STREAM_REFERER}
        response = requests.get(m3u_url, headers=headers, timeout=10)
        if response.status_code == 200:
            lines = response.text.splitlines()
            playlist = []
            pending_title = None
            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith('#EXTINF'):
                    match = re.search(r',(.+)$', line)
                    pending_title = match.group(1).strip() if match else None
                elif not line.startswith('#') and line.startswith('http'):
                    title = pending_title or os.path.basename(line.split('?')[0])
                    playlist.append({"url": line, "title": title})
                    pending_title = None
            return playlist
    except Exception as e:
        print(f"⚠️ M3U çekme hatası: {e}")
    return [{"url": m3u_url, "title": os.path.basename(m3u_url)}]


def download_logo():
    headers = {'User-Agent': STREAM_USER_AGENT}
    try:
        response = requests.get(LOGO_URL, headers=headers, timeout=10)
        if response.status_code == 200 and len(response.content) > 0:
            with open('logo.png', 'wb') as f:
                f.write(response.content)
            print("✅ Logo başarıyla indirildi.")
    except Exception as e:
        print(f"⚠️ Logo indirme hatası: {e}")


def write_title_file(title):
    try:
        with open('title.txt', 'w', encoding='utf-8') as f:
            f.write(title)
    except Exception as e:
        print(f"⚠️ Başlık dosyası yazma hatası: {e}")


def write_remaining_time_file(remaining_seconds):
    try:
        formatted = format_hms(remaining_seconds)
        with open('time.txt', 'w', encoding='utf-8') as f:
            f.write(formatted)
    except Exception as e:
        print(f"⚠️ Kalan süre dosyası yazma hatası: {e}")


def print_dashboard(title, index, playlist_len, seconds, status="🟢 Yayında"):
    print("┌" + "─" * 58 + "┐")
    print(f"│ 🎬 İçerik         : {title[:36]:<36} │")
    print(f"│ 🔢 Sıra           : {index + 1}/{playlist_len:<32} │")
    print(f"│ ⏱️  Geçen Süre     : {format_hms(seconds):<36} │")
    print(f"│ 📡 Durum          : {status:<36} │")
    print("└" + "─" * 58 + "┘")


def write_step_summary(title, index, playlist_len, seconds, status="🟢 Yayında"):
    if not GITHUB_STEP_SUMMARY:
        return
    try:
        content = (
            "## 📺 Canlı Yayın Durumu (Maxanimasyon)\n\n"
            "| Alan | Değer |\n"
            "|---|---|\n"
            f"| 🎬 Şu an oynayan içerik | {title} |\n"
            f"| 🔢 Playlist sırası | {index + 1} / {playlist_len} |\n"
            f"| ⏱️ Geçen süre | {format_hms(seconds)} (sa:dk:sn) |\n"
            f"| 📡 Durum | {status} |\n"
            f"| 🕒 Son güncelleme | {time.strftime('%Y-%m-%d %H:%M:%S')} |\n"
        )
        with open(GITHUB_STEP_SUMMARY, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"⚠️ Step summary yazma hatası: {e}")


def start_m3u_stream():
    download_logo()

    current_index, last_seconds, last_url, last_title = get_local_state()

    consecutive_fast_failures = 0
    FAST_FAIL_THRESHOLD_SECONDS = 20

    while True:
        playlist = get_m3u_playlist(M3U_URL)
        if not playlist:
            time.sleep(2)
            continue

        if current_index >= len(playlist):
            current_index = 0
            last_seconds = 0
            last_url = ""
            last_title = ""

        current_item = playlist[current_index]
        target_stream_url = current_item["url"]
        film_title = current_item["title"]

        if last_seconds > 0 and last_url and target_stream_url != last_url:
            if last_title and film_title == last_title:
                last_seconds = max(0, last_seconds - LINK_CHANGE_REWIND_SECONDS)
            else:
                last_seconds = 0

        last_url = target_stream_url
        last_title = film_title

        write_title_file(film_title)

        # En yüksek kalite HLS adresi
        real_stream_url = get_highest_quality_hls_link(target_stream_url)

        probe_url = real_stream_url.split(";")[0].strip() if ";" in real_stream_url else real_stream_url
        total_duration_sec = get_video_duration_ffprobe(probe_url)

        initial_remaining = max(0, total_duration_sec - last_seconds) if total_duration_sec > 0 else 0
        write_remaining_time_file(initial_remaining)

        print("=" * 60)
        print(f"📺 Oynatılan Film: {film_title}")
        print("⚡ Yıldırım Hızında Bağlantı Kuruluyor...")

        headers_arg = (
            f"User-Agent: {STREAM_USER_AGENT}\r\n"
            f"Referer: https://vidmody.com/\r\n"
            f"Origin: https://vidmody.com\r\n"
        )

        # Hızlı başlatma ve minimal gecikme parametreleri eklendi
        input_options = [
            '-re',
            '-headers', headers_arg,
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            '-err_detect', 'ignore_err',
            '-fflags', '+genpts+discardcorrupt+fastseek+nobuffer',
            '-allowed_extensions', 'ALL',
            '-thread_queue_size', '1024',
            '-max_interleave_delta', '0',
            '-analyzeduration', '5000000',
            '-probesize', '5000000',
            '-reconnect', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '2',
            '-rw_timeout', '5000000',
            '-threads', DECODER_THREADS,
        ]

        seek_args = ['-ss', str(last_seconds)] if last_seconds > 0 else []

        if ";" in real_stream_url:
            video_url, audio_url = real_stream_url.split(";", 1)
            video_url = video_url.strip()
            audio_url = audio_url.strip()

            input_args = (
                input_options + seek_args + ['-i', video_url] +
                input_options + seek_args + ['-i', audio_url]
            )
            logo1_input_index = 2
        else:
            input_args = input_options + seek_args + ['-i', real_stream_url]
            logo1_input_index = 1

        print_dashboard(film_title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")
        write_step_summary(film_title, current_index, len(playlist), last_seconds, status="🟡 Başlatılıyor")

        has_logo1 = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0

        title_drawtext = (
            f"drawtext=textfile='title.txt':reload=1:fontfile='{BOLD_FONT_PATH}':"
            f"fontcolor=white@{TEXT_OPACITY}:fontsize=19:"
            f"x=w-tw-20:y=h-th-20"
        )

        time_drawtext = (
            f"drawtext=textfile='time.txt':reload=1:fontfile='{BOLD_FONT_PATH}':"
            f"fontcolor=white@{TEXT_OPACITY}:fontsize=18:"
            f"x=20:y=h-th-20"
        )

        if has_logo1:
            logo_inputs = ['-i', 'logo.png']
            filter_str = (
                '[0:v]scale=1920:1080:flags=bicubic,setdar=16/9,fps=25[main];'
                f'[{logo1_input_index}:v]scale=-2:85,format=rgba,'
                f'colorchannelmixer=aa={LOGO_OPACITY}[logo1];'
                '[main][logo1]overlay=50:50[tmp1];'
                f'[tmp1]{title_drawtext}[tmp2];'
                f'[tmp2]{time_drawtext}[v]'
            )
        else:
            logo_inputs = []
            filter_str = (
                '[0:v]scale=1920:1080:flags=bicubic,setdar=16/9,fps=25[main];'
                f'[main]{title_drawtext}[tmp2];'
                f'[tmp2]{time_drawtext}[v]'
            )

        command = [
            'ffmpeg'
        ] + input_args + logo_inputs + [
            '-filter_complex', filter_str,
            '-map', '[v]',
            '-map', '1:a:0?' if ";" in real_stream_url else '0:a:0?',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',       # En hızlı geçiş ve sıfır yükleme süresi için
            '-tune', 'zerolatency',       # Anlık aktarım için gecikmeyi sıfırlama
            '-pix_fmt', 'yuv420p',
            '-r', '25',
            '-b:v', '2500k',
            '-maxrate', '2500k',
            '-bufsize', '2500k',
            '-g', '50',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ac', '2',
            '-ar', '44100',
            '-flvflags', 'no_duration_filesize', # RTMP paketlerini bekletmeden anında yolla
            '-f', 'flv',
            RTMP_SERVER
        ]

        process = subprocess.Popen(
            command,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )

        last_save_time = time.time()
        last_dashboard_time = time.time()
        current_stream_seconds = last_seconds
        stderr_tail = deque(maxlen=40)
        last_progress_time = [time.time()]

        def _watchdog(proc=process, progress_ref=last_progress_time):
            while proc.poll() is None:
                time.sleep(3)
                if time.time() - progress_ref[0] > WATCHDOG_TIMEOUT_SECONDS:
                    print(f"🚨 Watchdog: İlerleme durdu. Hızlıca yeniden başlatılıyor.")
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    break

        watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
        watchdog_thread.start()

        while True:
            line = process.stderr.readline()
            if not line and process.poll() is not None:
                break

            if line:
                stderr_tail.append(line.rstrip())

            if "time=" in line:
                time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                if time_match:
                    hrs, mins, secs = time_match.groups()
                    played_seconds = int(hrs) * 3600 + int(mins) * 60 + float(secs)
                    current_stream_seconds = last_seconds + played_seconds

                    if total_duration_sec > 0:
                        remaining_seconds = max(0, total_duration_sec - current_stream_seconds)
                        write_remaining_time_file(remaining_seconds)
                    else:
                        write_remaining_time_file(current_stream_seconds)

                    now = time.time()
                    last_progress_time[0] = now

                    if now - last_save_time > 30:
                        update_local_state(current_index, current_stream_seconds, target_stream_url, film_title)
                        last_save_time = now

                    if now - last_dashboard_time > 30:
                        print_dashboard(film_title, current_index, len(playlist), current_stream_seconds)
                        write_step_summary(film_title, current_index, len(playlist), current_stream_seconds)
                        last_dashboard_time = now

        if process.returncode == 0:
            print("⚡ Film bitti, anında sonraki filme geçiliyor...")
            current_index += 1
            last_seconds = 0
            last_url = ""
            last_title = ""
            update_local_state(current_index, 0, "", "")
            consecutive_fast_failures = 0
            # Bekleme süresi 0 yapıldı, sıradaki filme anında bağlanacak
            time.sleep(0.1)
        else:
            print(f"⚠️ Yayın koptu (Return Code: {process.returncode}). Yeniden deneniyor...")
            duration_this_attempt = current_stream_seconds - last_seconds
            if duration_this_attempt < FAST_FAIL_THRESHOLD_SECONDS:
                consecutive_fast_failures += 1
            else:
                consecutive_fast_failures = 0

            if consecutive_fast_failures >= 3:
                current_index += 1
                last_seconds = 0
                last_url = ""
                last_title = ""
                consecutive_fast_failures = 0
                update_local_state(current_index, 0, "", "")
                time.sleep(0.5)
            else:
                last_seconds = current_stream_seconds
                last_url = target_stream_url
                update_local_state(current_index, last_seconds, last_url, film_title)
                time.sleep(0.5)


if __name__ == "__main__":
    start_m3u_stream()
