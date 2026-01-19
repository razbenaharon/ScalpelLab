import os
import sys
import math
import time
import atexit
from tkinter import Tk, Toplevel, Button, Label, Frame, IntVar, Radiobutton, ttk
from tkinter.filedialog import askopenfilenames, askopenfilename, askdirectory
from tkinter import messagebox
import configparser
import subprocess
from typing import List

from lib.mpv_controller import MPVController
from lib.models import Camera, CameraMetadata
from lib.sync_panel import SyncPanel
from lib.db_browser import DatabaseBrowser

FILE_EXTENSION = ".mmpv"


class multiMPV:
    def __init__(self, cwd):
        self.__cwd = cwd
        self.__config_file_path = os.path.join(cwd, 'config.ini')
        self.__first_run = False
        self.__config = configparser.ConfigParser()
        self.__config.read(self.__config_file_path)
        self.__filetypes = [
            ("video file", FILE_EXTENSION),
            ("video file", ".mp4"),
            ("video file", ".MP4"),
            ("video file", ".mkv"),
            ("video file", ".MKV"),
            ("video file", ".avi"),
            ("video file", ".AVI"),
            ("video file", ".txt")
        ]
        if 'first_run' in self.__config['MPV']:
            self.__first_run = True
            with open(self.__config_file_path, 'w') as conf:
                self.__config.remove_option('MPV', 'first_run')
                self.__config.write(conf)
        video_scale = self.__config['MPV']['video_scale']
        force_original_aspect_ratio = self.__config['MPV']['force_original_aspect_ratio']
        self.__video_scale = f'scale={video_scale}:force_original_aspect_ratio={force_original_aspect_ratio},pad={video_scale}:-1:-1:color=black'
        self.__relative_vid_path = os.getcwd()
        self.__filenames = None
        self.__external_file_stack = None
        self.mpv_dir = None

    def _is_video_extension(self, file):
        return any([file.endswith(ext) for ext in self.__filetypes])

    def get_vids(self):
        vids = []
        last_dir = "C:/"
        
        # Default to directory mode
        directory = askdirectory(initialdir=last_dir, title='select video directory')
        if directory:
            vids = self.get_vids_from_dir(directory)
        return vids
        
    def get_vids_from_dir(self, directory):
        valid_exts = (".mp4", ".MP4", ".mkv", ".MKV", ".avi", ".AVI")
        vids = []
        
        for root, dirs, files in os.walk(directory):
            for file in files:
                if file.endswith(valid_exts):
                    vids.append(os.path.join(root, file))
                    if len(vids) >= 9:
                        break
            if len(vids) >= 9:
                break
        
        print(f"Found files: {[os.path.basename(v) for v in vids]}")
        
        if len(vids) >= 9:
            print(f"Warning: Hit limit of 9 videos.")
        
        return vids

    @staticmethod
    def get_vids_from_txt(txt_files):
        vids = []
        for txt_file in txt_files:
            with open(txt_file) as f:
                for vid in f:
                    vid_path = vid.strip('\n')
                    vids.append(vid_path)
        return vids

    def assert_vids_location(self, vids):
        assert len(vids) >= 1, 'no video files were selected'
        assert len(vids) <= 9, 'multiMPV support up to 9 video files'
        for i, vid in enumerate(vids):
            if os.path.exists(os.path.join(self.__relative_vid_path, vid)):
                vids[i] = os.path.join(self.__relative_vid_path, vids[i])
            else:
                if not os.path.exists(vid):
                    messagebox.showwarning(title="Exception", message=f"{vid} was not found")
                    sys.exit()

    def _assert_mpv_exe(self):
        def mpv_file_picker():
            mbox_select = messagebox.askokcancel(title="mpv executable not found",
                                                 message="Please select an mpv executable file")
            if not mbox_select:
                sys.exit()
            filenames = askopenfilename(initialdir="/", title='Select mpv executable',
                                        filetypes=[("executable", ".exe")])
            if not filenames:
                sys.exit()
            return filenames

        mpv_path = self.__config['MPV']['mpv_path']

        if not os.path.exists(mpv_path):
            mpv_path = mpv_file_picker()
            try:
                with open(self.__config_file_path, 'w') as conf:
                    self.__config['MPV']['mpv_path'] = mpv_path
                    self.__config.write(conf)
                if self.__first_run:
                    sys.exit()
            except OSError:
                warning = f'Failed to save the mpv path. please change the "mpv_path" field in the config.ini file'
                messagebox.showwarning(title="Warning", message=warning)

        mpv_dir = os.path.dirname(mpv_path)
        return mpv_dir

    def _launch_session(self, metadata_list: List[CameraMetadata], mpv_dir: str, root: Tk = None):
        """
        Launch MPV session with provided camera metadata.
        This handles grid calculation, process launching, and SyncPanel init.
        """
        n = len(metadata_list)
        if n == 0: return
        
        # Calculate grid
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)
        
        # Root handling
        if root is None:
            # CLI mode or fresh start
            root = Tk()
            root.withdraw()
        
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        
        usable_width = screen_width
        usable_height = screen_height - 100 
        
        win_width = int(usable_width / cols)
        win_height = int(usable_height / rows)
        
        # Initialize Controller
        mpv_exe = os.path.join(mpv_dir, 'mpv.exe')
        controller = MPVController(mpv_exe)
        
        cameras = []
        
        # Normalize start offsets to ensure no negative values for MPV
        # Find minimum offset
        min_offset = 0.0
        if metadata_list:
            min_offset = min(meta.offset_seconds for meta in metadata_list)
        
        # Calculate shift if needed (only if min is negative)
        shift = 0.0
        if min_offset < 0:
            shift = abs(min_offset)
            print(f"Normalizing offsets: shifting all start times by +{shift}s")

        for i, meta in enumerate(metadata_list):
            r = i // cols
            c = i % cols
            x = c * win_width
            y = r * win_height
            
            # Use 4 backslashes in literal to get 2 in string: \\.\pipe\...
            pipe_name = f'\\\\.\\pipe\\mpv_socket_{i}_{int(time.time())}'
            geometry = f"{win_width}x{win_height}+{x}+{y}"
            
            # Apply shift to launch start time
            launch_start = meta.offset_seconds + shift
            
            print(f"Launching: {meta.camera_name} ({meta.file_path}) Offset: {meta.offset_seconds}s -> Start: {launch_start}s")
            try:
                # Launch with normalized start time
                process = controller.launch_video(
                    video_path=meta.file_path, 
                    pipe_name=pipe_name, 
                    geometry=geometry,
                    start_offset=launch_start
                )
                
                # Create Camera object (keep original DB offset)
                camera = Camera(
                    name=meta.camera_name,
                    file_path=meta.file_path,
                    case_id=meta.case_id if meta.case_id else ("unknown", 0),
                    mpv_process=process,
                    ipc_pipe_path=pipe_name,
                    offset_seconds=meta.offset_seconds # Initialize with saved offset
                )
                cameras.append(camera)
            except Exception as e:
                print(f"Failed to launch {meta.camera_name}: {e}")
            
        # Control Window
        if cameras:
            control_window = Toplevel(root)
            
            app = SyncPanel(control_window, cameras, controller)
            
            def cleanup():
                app.running = False
                controller.close_all()
                root.destroy() # Kill everything
                sys.exit(0)
                
            control_window.protocol("WM_DELETE_WINDOW", cleanup)
            atexit.register(cleanup)
            
            # Only call mainloop if we created the root or if we are the main driver
            # In GUI mode, mainloop is called by _show_startup_dialog
            if root.state() == 'normal' or root.state() == 'withdrawn': 
                 # If we are in CLI mode (root created here), block
                 # If we are in GUI mode, we are already in mainloop?
                 # Actually, _show_startup_dialog calls mainloop().
                 # But we are called FROM a callback inside mainloop.
                 # So we don't need to call mainloop again.
                 pass
                 
        else:
            print("No cameras launched.")
            root.destroy()
            sys.exit()

    def run_independent(self, vids, mpv_dir, root: Tk = None):
        """Legacy entry point: convert paths to metadata and launch"""
        metadata_list = []
        for vid in vids:
            name = os.path.basename(vid)
            # Create simple metadata with 0 offset
            meta = CameraMetadata(
                camera_name=name,
                file_path=vid,
                duration=0,
                file_size=0,
                offset_seconds=0.0
            )
            metadata_list.append(meta)
            
        self._launch_session(metadata_list, mpv_dir, root)

    def _show_startup_dialog(self):
        """Show dialog to choose between File or Database load"""
        root = Tk()
        root.title("MultiMPV - Select Source")
        root.geometry("400x300")
        
        style = ttk.Style()
        style.theme_use('clam')
        
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill='both', expand=True)
        
        ttk.Label(frame, text="Welcome to ScalpelLab Video Review", font=("Arial", 14)).pack(pady=20)
        
        def on_file():
            root.withdraw() # Hide startup
            vids = self.get_vids()
            if vids:
                self.assert_vids_location(vids)
                self.run_independent(vids, self.mpv_dir, root)
            else:
                root.destroy()
                sys.exit()

        def on_db():
            # Hide startup, open DB browser
            root.withdraw()
            
            def on_cameras_selected(cameras: List[CameraMetadata]):
                # Callback when cameras selected from DB
                # Close DB browser is handled by browser class
                # Launch session
                self._launch_session(cameras, self.mpv_dir, root)

            try:
                DatabaseBrowser(root, on_cameras_selected)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to open database browser: {e}")
                root.deiconify() # Show startup again

        btn_file = ttk.Button(frame, text="Load from Local Files", command=on_file)
        btn_file.pack(fill='x', pady=10, ipady=10)
        
        btn_db = ttk.Button(frame, text="Load from Database", command=on_db)
        btn_db.pack(fill='x', pady=10, ipady=10)
        
        ttk.Button(frame, text="Exit", command=root.destroy).pack(fill='x', pady=20)
        
        root.mainloop()

    def run(self, txt_file=None):
        self.mpv_dir = self._assert_mpv_exe()
        
        if txt_file:
            # CLI mode
            vids = self.get_vids_from_txt(txt_file)
            if not vids: sys.exit()
            self.assert_vids_location(vids)
            self.run_independent(vids, self.mpv_dir)
        else:
            # GUI mode - Show Startup Dialog
            self._show_startup_dialog()


if __name__ == '__main__':
    # Get the directory containing this script
    cwd = os.path.dirname(os.path.abspath(__file__))
    if cwd.endswith(".zip"):
        cwd = os.path.dirname(cwd)
    multi_mpv = multiMPV(cwd)
    if len(sys.argv) != 2:
        multi_mpv.run()
    else:
        txt_file = sys.argv[1]
        if not isinstance(txt_file, list):
            txt_file = [txt_file]
        multi_mpv.run(txt_file)
