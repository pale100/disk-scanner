from flask import Flask, render_template, request, jsonify, Response
import os, string, time, threading, queue, subprocess, json, shutil

app = Flask(__name__)

SKIP = {"$recycle.bin", "system volume information", "windows.old", "$windows.~bt", "$windows.~ws", "msocache"}

def get_drives():
    out = []
    for d in string.ascii_uppercase:
        p = f"{d}:\\"
        if os.path.exists(p):
            try:
                total, used, free = 0, 0, 0
                import shutil
                t = shutil.disk_usage(p)
                out.append({"letter": p, "total": t.total, "free": t.free})
            except Exception:
                out.append({"letter": p, "total": 0, "free": 0})
    return out

def fast_scan(drive, terms, q, stop, limit, counter):
    stack = [drive]
    while stack and not stop["s"]:
        path = stack.pop()
        try:
            with os.scandir(path) as it:
                for e in it:
                    if stop["s"]:
                        return
                    try:
                        name_l = e.name.lower()
                        counter["files"] += 1
                        if counter["files"] & 511 == 0:
                            counter["current"] = e.path
                        is_dir = False
                        try:
                            is_dir = e.is_dir(follow_symlinks=False)
                        except Exception:
                            pass
                        if all(t in name_l for t in terms):
                            size = 0
                            if not is_dir:
                                try:
                                    size = e.stat(follow_symlinks=False).st_size
                                except Exception:
                                    pass
                            q.put({"path": e.path, "size": size, "drive": drive, "dir": is_dir})
                            counter["hits"] += 1
                            if counter["hits"] >= limit:
                                stop["s"] = True
                                return
                        if is_dir and name_l not in SKIP:
                            stack.append(e.path)
                    except Exception:
                        pass
        except Exception:
            pass

@app.route("/")
def index():
    return render_template("index.html", drives=get_drives())

@app.route("/search")
def search():
    term = request.args.get("q", "").strip()
    drives_param = request.args.get("drives", "")
    limit = int(request.args.get("limit", 2000))
    terms = [t.lower() for t in term.split() if t]
    drives = [d for d in drives_param.split(",") if d and os.path.exists(d)]

    def gen():
        if not terms or not drives:
            yield f"event: done\ndata: {json.dumps({'elapsed': 0, 'scanned': 0})}\n\n"
            return
        q = queue.Queue()
        stop = {"s": False}
        counter = {"files": 0, "hits": 0, "current": ""}
        threads = []
        for d in drives:
            t = threading.Thread(target=fast_scan, args=(d, terms, q, stop, limit, counter), daemon=True)
            t.start()
            threads.append(t)
        start = time.time()
        last_p = 0
        batch = []
        last_flush = time.time()
        while any(t.is_alive() for t in threads) or not q.empty():
            try:
                while True:
                    batch.append(q.get_nowait())
                    if len(batch) >= 50:
                        break
            except queue.Empty:
                pass
            now = time.time()
            if batch and (len(batch) >= 50 or now - last_flush > 0.15):
                yield f"event: results\ndata: {json.dumps(batch)}\n\n"
                batch = []
                last_flush = now
            if now - last_p > 0.2:
                yield f"event: progress\ndata: {json.dumps({'scanned': counter['files'], 'hits': counter['hits'], 'current': counter['current'], 'elapsed': round(now - start, 1)})}\n\n"
                last_p = now
            if not batch:
                time.sleep(0.03)
        if batch:
            yield f"event: results\ndata: {json.dumps(batch)}\n\n"
        yield f"event: done\ndata: {json.dumps({'elapsed': round(time.time() - start, 2), 'scanned': counter['files'], 'hits': counter['hits']})}\n\n"

    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/open", methods=["POST"])
def open_path():
    data = request.get_json(silent=True) or {}
    p = data.get("path", "")
    if not p or not os.path.exists(p):
        return jsonify({"ok": False, "error": "not found"}), 404
    try:
        if os.path.isdir(p):
            os.startfile(p)
        else:
            subprocess.Popen(["explorer", "/select,", p])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/copy_to_desktop", methods=["POST"])
def copy_to_desktop():
    data = request.get_json(silent=True) or {}
    p = data.get("path", "")
    if not p or not os.path.exists(p):
        return jsonify({"ok": False, "error": "not found"}), 404
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):
            desktop = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
        name = os.path.basename(p.rstrip("\\/"))
        target = os.path.join(desktop, name)
        i = 1
        base, ext = os.path.splitext(name)
        while os.path.exists(target):
            target = os.path.join(desktop, f"{base} ({i}){ext}")
            i += 1
        if os.path.isdir(p):
            shutil.copytree(p, target)
        else:
            shutil.copy2(p, target)
        return jsonify({"ok": True, "target": target})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=False, threaded=True, port=57890)
