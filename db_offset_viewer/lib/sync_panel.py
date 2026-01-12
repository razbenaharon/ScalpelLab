"""
Sync Panel Module

Provides the user interface for manual camera synchronization.
Includes:
- Real-time timestamp display
- Sync status indicators
- Individual seek controls (replacing sliders)
- Nudge controls
- Database persistence

Updated for feature: Seek buttons instead of sliders
"""

import tkinter as tk
from tkinter import ttk, messagebox, Canvas, Scrollbar
import threading
import time
from typing import List, Optional, Dict
from .models import Camera
from .mpv_controller import MPVController

class CameraControlRow:
    """Helper class to manage UI widgets for a single camera row"""
    def __init__(self, parent, camera: Camera, controller: MPVController, on_nudge):
        self.camera = camera
        self.controller = controller
        self.on_nudge = on_nudge
        
        self.frame = ttk.Frame(parent, padding=5, relief="groove")
        self.frame.pack(fill=tk.X, pady=2, padx=5)
        
        # --- Header: Name + Status ---
        header = ttk.Frame(self.frame)
        header.pack(fill=tk.X)
        
        ttk.Label(header, text=camera.name, font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        self.status_label = ttk.Label(header, text="Waiting...", font=("Arial", 9))
        self.status_label.pack(side=tk.RIGHT)
        
        # --- Seek Buttons (Replaces Slider) ---
        seek_frame = ttk.Frame(self.frame)
        seek_frame.pack(fill=tk.X, pady=2)
        
        # Define jumps: (Label, Seconds)
        jumps = [
            ("-1h", -3600), ("-20m", -1200), ("-5m", -300), ("-1m", -60),
            ("+1m", 60), ("+5m", 300), ("+20m", 1200), ("+1h", 3600)
        ]
        
        # Center the buttons
        btn_container = ttk.Frame(seek_frame)
        btn_container.pack(anchor="center")
        
        for label, sec in jumps:
            btn = ttk.Button(btn_container, text=label, width=5,
                             command=lambda s=sec: self._seek(s))
            btn.pack(side=tk.LEFT, padx=1)

        # --- Controls: Time | Offset | Buttons ---
        controls = ttk.Frame(self.frame)
        controls.pack(fill=tk.X)
        
        self.time_label = ttk.Label(controls, text="0.00s", width=10)
        self.time_label.pack(side=tk.LEFT)
        
        ttk.Label(controls, text="Offset:").pack(side=tk.LEFT, padx=(10, 2))
        self.offset_label = ttk.Label(controls, text=f"{camera.offset_seconds:+.2f}s", width=8)
        self.offset_label.pack(side=tk.LEFT)
        
        # Nudge Buttons
        btn_frame = ttk.Frame(controls)
        btn_frame.pack(side=tk.RIGHT)
        
        ttk.Button(btn_frame, text="+1.0s", width=5, command=lambda: self.on_nudge(camera, 1.0)).pack(side=tk.RIGHT, padx=1)
        ttk.Button(btn_frame, text="+0.1s", width=5, command=lambda: self.on_nudge(camera, 0.1)).pack(side=tk.RIGHT, padx=1)
        ttk.Button(btn_frame, text="-0.1s", width=5, command=lambda: self.on_nudge(camera, -0.1)).pack(side=tk.RIGHT, padx=1)
        ttk.Button(btn_frame, text="-1.0s", width=5, command=lambda: self.on_nudge(camera, -1.0)).pack(side=tk.RIGHT, padx=1)

    def _seek(self, seconds):
        """Seek individual camera relative"""
        self.controller.send_command(self.camera.ipc_pipe_path, f"seek {seconds} relative+exact")

    def update(self):
        """Update UI from camera state"""
        if not self.frame.winfo_exists(): return
        
        self.time_label.configure(text=f"{self.camera.current_timestamp:.2f}s")
        self.offset_label.configure(text=f"{self.camera.offset_seconds:+.2f}s")
        
        self.status_label.configure(text=self.camera.sync_status_text)
        if self.camera.sync_status == "synced":
            self.status_label.configure(foreground="green")
        elif self.camera.sync_status == "out_of_sync":
            self.status_label.configure(foreground="orange")
        else:
            self.status_label.configure(foreground="black")

class SyncPanel:
    """
    Main control panel for multi-camera synchronization.
    """

    def __init__(self, master: tk.Toplevel, cameras: List[Camera], controller: MPVController):
        self.master = master
        self.cameras = cameras
        self.controller = controller
        
        self.running = True
        self.reference_camera: Optional[Camera] = cameras[0] if cameras else None
        
        # Mark first camera as reference by default
        if self.reference_camera:
            self.reference_camera.is_reference = True

        self.master.title("MultiMPV Sync Control")
        self.master.geometry("900x800") # Increased height for buttons
        
        # Map camera name to control row
        self.camera_rows: Dict[str, CameraControlRow] = {}
        
        self._build_ui()
        
        # Start polling thread
        self.poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.poll_thread.start()
        
        # Cleanup on close
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)
        
        # Force initial update
        self.master.after(100, self._update_ui)

    def _build_ui(self):
        """Construct the UI layout"""
        main_frame = ttk.Frame(self.master, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # --- Top: Master Controls ---
        control_frame = ttk.LabelFrame(main_frame, text="Master Playback Controls", padding=5)
        control_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Master Seek Buttons (Replaces Timeline)
        seek_frame = ttk.Frame(control_frame)
        seek_frame.pack(fill=tk.X, pady=5)
        
        # Current time display
        self.master_time_label = ttk.Label(seek_frame, text="00:00:00", font=("Arial", 12))
        self.master_time_label.pack(anchor="center")
        
        # Buttons container
        btn_container = ttk.Frame(seek_frame)
        btn_container.pack(anchor="center", pady=5)
        
        jumps = [
            ("-1h", -3600), ("-20m", -1200), ("-5m", -300), ("-1m", -60),
            ("+1m", 60), ("+5m", 300), ("+20m", 1200), ("+1h", 3600)
        ]
        
        for label, sec in jumps:
            btn = ttk.Button(btn_container, text=label, width=5,
                             command=lambda s=sec: self._send_global(f"seek {s} relative+exact"))
            btn.pack(side=tk.LEFT, padx=1)

        # Standard Transport Controls
        transport_frame = ttk.Frame(control_frame)
        transport_frame.pack(anchor=tk.CENTER, pady=5)
        
        ttk.Button(transport_frame, text="< Frame", command=lambda: self._send_global("frame-back-step")).pack(side=tk.LEFT, padx=2)
        ttk.Button(transport_frame, text="<< 10s", command=lambda: self._send_global("seek -10")).pack(side=tk.LEFT, padx=5)
        ttk.Button(transport_frame, text="Play All", command=lambda: self._send_global("set pause no")).pack(side=tk.LEFT, padx=5)
        ttk.Button(transport_frame, text="Pause All", command=lambda: self._send_global("set pause yes")).pack(side=tk.LEFT, padx=5)
        ttk.Button(transport_frame, text="10s >>", command=lambda: self._send_global("seek 10")).pack(side=tk.LEFT, padx=5)
        ttk.Button(transport_frame, text="Frame >", command=lambda: self._send_global("frame-step")).pack(side=tk.LEFT, padx=2)

        # Speed
        ttk.Label(transport_frame, text="Speed:").pack(side=tk.LEFT, padx=(20, 5))
        self.speed_var = tk.StringVar(value="1.0x")
        speed_combo = ttk.Combobox(transport_frame, textvariable=self.speed_var, values=["0.25x", "0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"], width=5, state="readonly")
        speed_combo.pack(side=tk.LEFT)
        speed_combo.bind("<<ComboboxSelected>>", self._on_speed_changed)

        # --- Middle: Scrollable Camera List ---
        list_frame = ttk.LabelFrame(main_frame, text="Individual Camera Controls", padding=5)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Scrollable Canvas
        canvas = Canvas(list_frame)
        scrollbar = Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        self.scrollable_frame = ttk.Frame(canvas)
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        # Make the inner window responsive to canvas width
        win_id = canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        
        def on_canvas_configure(event):
            canvas.itemconfig(win_id, width=event.width)
        canvas.bind("<Configure>", on_canvas_configure)

        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Create Rows
        for cam in self.cameras:
            row = CameraControlRow(self.scrollable_frame, cam, self.controller, self._nudge_camera)
            self.camera_rows[cam.name] = row

        # --- Bottom: Global Actions ---
        bottom_frame = ttk.Frame(main_frame, padding=5)
        bottom_frame.pack(fill=tk.X, pady=10)
        
        self.save_btn = ttk.Button(bottom_frame, text="Save All Offsets", command=self._save_offsets_to_database, state=tk.DISABLED)
        self.save_btn.pack(side=tk.LEFT)
        
        self.reset_btn = ttk.Button(bottom_frame, text="Restart Videos", command=self._restart_video)
        self.reset_btn.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(bottom_frame, text="Exit", command=self._on_close).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom_frame, text="Export Annotations", command=self._export_annotations).pack(side=tk.RIGHT)
        ttk.Button(bottom_frame, text="Mark Timestamp", command=self._mark_timestamp).pack(side=tk.RIGHT, padx=10)

        self.annotations = []

    def _restart_video(self):
        """Rewind all videos to 00:00:00 (keeps sync offsets)"""
        self._send_global("seek 0 absolute")

    def _nudge_camera(self, camera: Camera, amount: float):
        """Callback for nudge buttons"""
        camera.offset_seconds += amount
        camera.offset_modified = True
        
        # Apply relative seek
        self.controller.send_command(camera.ipc_pipe_path, f"seek {amount} relative+exact")
        
        # UI updates automatically via polling loop, but we can force update for immediate feedback
        if camera.name in self.camera_rows:
            self.camera_rows[camera.name].update()

    def _poll_loop(self):
        """Background thread for querying timestamps"""
        while self.running:
            start_time = time.time()
            
            # Query all cameras
            for cam in self.cameras:
                if not self.running: break
                
                # Skip if process is dead
                if cam.mpv_process.poll() is not None:
                    cam.sync_status = "error"
                    cam.sync_status_text = "Exited"
                    continue

                # Timestamp
                val = self.controller.query_property(cam.ipc_pipe_path, "time-pos")
                if val:
                    try:
                        cam.current_timestamp = float(val)
                    except ValueError:
                        pass
                
                # Duration (continuously update)
                dur = self.controller.query_property(cam.ipc_pipe_path, "duration")
                if dur:
                    try:
                        d = float(dur)
                        if d > 0 and abs(cam.duration - d) > 1.0:
                            cam.duration = d
                    except ValueError:
                        pass

                # Update sync status if we have a reference
                if self.reference_camera:
                    ref_ts = self.reference_camera.current_timestamp
                    cam.update_sync_status(ref_ts)

            # Schedule UI update on main thread
            if self.running:
                try:
                    self.master.after(0, self._update_ui)
                except Exception:
                    # Master likely destroyed
                    self.running = False
                    break
            
            elapsed = time.time() - start_time
            sleep_time = max(0.01, 0.1 - elapsed)
            time.sleep(sleep_time)

    def _update_ui(self):
        """Update all UI elements"""
        try:
            if not self.master.winfo_exists():
                self.running = False
                return
        except Exception:
            self.running = False
            return

        # Update Individual Rows
        for row in self.camera_rows.values():
            try:
                row.update()
            except Exception:
                pass # Widget destroyed?
            
        # Update Master Time Display
        if self.reference_camera:
            ts = self.reference_camera.current_timestamp
            # Format HH:MM:SS
            m, s = divmod(int(ts), 60)
            h, m = divmod(m, 60)
            self.master_time_label.configure(text=f"{h:02d}:{m:02d}:{s:02d}")

        # Enable save button
        try:
            if any(c.offset_modified for c in self.cameras):
                self.save_btn.configure(state=tk.NORMAL)
            else:
                self.save_btn.configure(state=tk.DISABLED)
        except Exception:
            pass

    def _send_global(self, command: str):
        """Send command to all cameras"""
        for cam in self.cameras:
            self.controller.send_command(cam.ipc_pipe_path, command)

    def _on_speed_changed(self, event):
        speed_str = self.speed_var.get().replace("x", "")
        try:
            speed = float(speed_str)
            self._send_global(f"set speed {speed}")
            if speed != 1.0:
                self._send_global("set ao-volume 0")
            else:
                self._send_global("set ao-volume 100")
        except ValueError:
            pass

    def _mark_timestamp(self):
        if not self.reference_camera: return
        current_time = self.reference_camera.current_timestamp
        
        from tkinter import simpledialog
        text = simpledialog.askstring("Mark Timestamp", f"Annotation at {current_time:.2f}s:")
        
        if text:
            cam_states = {c.name: c.current_timestamp for c in self.cameras}
            note = {
                "timestamp": current_time,
                "text": text,
                "camera_times": cam_states,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            self.annotations.append(note)
            messagebox.showinfo("Marked", "Annotation saved.")

    def _export_annotations(self):
        if not self.annotations:
            messagebox.showinfo("Export", "No annotations to export.")
            return
            
        from tkinter import filedialog
        import json
        
        fpath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("Text files", "*.txt")]
        )
        
        if not fpath: return
        
        try:
            if fpath.endswith(".json"):
                with open(fpath, "w") as f:
                    json.dump(self.annotations, f, indent=2)
            else:
                with open(fpath, "w") as f:
                    for note in self.annotations:
                        f.write(f"[{note['timestamp']:.2f}s] {note['text']}\n")
                        for cam, t in note['camera_times'].items():
                            f.write(f"  - {cam}: {t:.2f}s\n")
                        f.write("\n")
            
            messagebox.showinfo("Export", f"Exported {len(self.annotations)} annotations.")
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to save: {e}")

    def _save_offsets_to_database(self):
        modified_cameras = [c for c in self.cameras if c.offset_modified]
        if not modified_cameras: return

        msg = "Save the following sync offsets to database?\n\n"
        for cam in modified_cameras:
            msg += f"{cam.name}: {cam.offset_seconds:+.2f}s\n"
        
        if not messagebox.askyesno("Confirm Save", msg): return

        import sqlite3
        import os
        try:
            # Database is located at db_offset_viewer root, go up one level from lib/
            db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ScalpelDatabase.sqlite")
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            for cam in modified_cameras:
                if cam.case_id:
                    recording_date, case_no = cam.case_id
                    cursor.execute("""
                        UPDATE mp4_status 
                        SET offset_seconds = ? 
                        WHERE recording_date = ? AND case_no = ? AND filename LIKE ?
                    """, (cam.offset_seconds, recording_date, case_no, f"%{cam.name}%"))
                    cam.offset_modified = False
            
            conn.commit()
            conn.close()
            messagebox.showinfo("Success", "Offsets saved.")
        except Exception as e:
            messagebox.showerror("Error", f"Database save failed: {e}")

    def _on_close(self):
        self.running = False
        self.controller.close_all()
        try:
            self.master.destroy()
        except Exception:
            pass