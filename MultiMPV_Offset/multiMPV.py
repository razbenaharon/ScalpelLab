import os
import sys
import math
import time
import atexit
from tkinter import Tk, Toplevel, Button, Label, Frame, IntVar, Radiobutton
from tkinter.filedialog import askopenfilenames, askopenfilename, askdirectory
from tkinter import messagebox
import configparser
import subprocess

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

    def _generate_scale(self, input_count):
        generated_scale = ''
        for i in range(1, input_count + 1):
            generated_scale += f'[vid{i}]' + self.__video_scale + f'[v{i}];'
        return generated_scale

    def _get_mpv_command(self, vids):
        input_count = len(vids)
        input_name = f"input\\input{input_count}.conf"
        input_conf = f'--input-conf="{os.path.join(self.__cwd, input_name)}"'
        script_name = f"scripts\\cycle-commands.lua"
        script = f'--script="{os.path.join(self.__cwd, script_name)}"'
        generated_scale = self._generate_scale(input_count)
        external_file_stack = '"' + ';'.join(vids[1:]) + '"'

        if len(vids) == 1:
            mpv_command = f' "{vids[0]}"'
        elif len(vids) == 2:
            mpv_command = f' {script} {input_conf} --lavfi-complex="[vid1]{self.__video_scale}[v1];[vid2]{self.__video_scale}[v2];[v1][v2]hstack[vo]" "{vids[0]}" --external-file={external_file_stack}'
        elif len(vids) == 3:
            mpv_command = f' {script} {input_conf} --lavfi-complex="{generated_scale}[v1][v2][v3]xstack=inputs=3:layout=0_0|w0_0|0_h0:fill=black[vo]" "{vids[0]}" --external-files={external_file_stack}'
        elif len(vids) == 4:
            mpv_command = f' {script} {input_conf} --lavfi-complex="{generated_scale}[v1][v2]hstack=inputs=2[top];[v3][v4]hstack=inputs=2[bottom];[top][bottom]vstack=inputs=2[vo]" "{vids[0]}" --external-files={external_file_stack}'
        elif len(vids) == 5:
            mpv_command = f' {script} {input_conf} --lavfi-complex="{generated_scale}[v1][v2][v3][v4][v5]xstack=inputs=5:layout=0_0|w0_0|w0+w1_0|0_h0|w0_h0|w0+w1_h0:fill=black[vo]" "{vids[0]}" --external-files={external_file_stack}'
        elif len(vids) == 6:
            mpv_command = f' {script} {input_conf} --lavfi-complex="{generated_scale}[v1][v2][v3]hstack=inputs=3[top];[v4][v5][v6]hstack=inputs=3[bottom];[top][bottom]vstack=inputs=2[vo]" "{vids[0]}" --external-files={external_file_stack}'
        elif len(vids) == 7:
            mpv_command = f' {script} {input_conf} --lavfi-complex="{generated_scale}[v1][v2][v3][v4][v5][v6][v7]xstack=inputs=7:layout=0_0|w0_0|w0+w1_0|0_h0|w0_h0|w0+w1_h0|0_h0+h1|w0_h0+h1|w0+w1_h0+h1:fill=black[vo]" "{vids[0]}" --external-files={external_file_stack}'
        elif len(vids) == 8:
            mpv_command = f' {script} {input_conf} --lavfi-complex="{generated_scale}[v1][v2][v3][v4][v5][v6][v7][v8]xstack=inputs=8:layout=0_0|w0_0|w0+w1_0|0_h0|w0_h0|w0+w1_h0|0_h0+h1|w0_h0+h1|w0+w1_h0+h1:fill=black[vo]" "{vids[0]}" --external-files={external_file_stack}'
        else:
            mpv_command = f' {script} {input_conf} --lavfi-complex="{generated_scale}[v1][v2][v3][v4][v5][v6][v7][v8][v9]xstack=inputs=9:layout=0_0|w0_0|w0+w1_0|0_h0|w0_h0|w0+w1_h0|0_h0+h1|w0_h0+h1|w0+w1_h0+h1[vo]" "{vids[0]}" --external-files={external_file_stack}'
        return mpv_command

    def run_independent(self, vids, mpv_dir):
        # Calculate grid
        n = len(vids)
        if n == 0: return
        
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)
        
        root = Tk()
        root.withdraw()
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        
        # Reserve some space for taskbar/controls (approx)
        usable_width = screen_width
        usable_height = screen_height - 100 
        
        win_width = int(usable_width / cols)
        win_height = int(usable_height / rows)
        
        processes = []
        pipes = []
        
        mpv_exe = os.path.join(mpv_dir, 'mpv.exe')
        
        for i, vid in enumerate(vids):
            r = i // cols
            c = i % cols
            x = c * win_width
            y = r * win_height
            
            pipe_name = f'\\\\.\\pipe\\mpv_socket_{i}_{int(time.time())}'
            pipes.append(pipe_name)
            
            cmd = [
                mpv_exe,
                vid,
                f'--geometry={win_width}x{win_height}+{x}+{y}',
                f'--input-ipc-server={pipe_name}',
                '--keep-open=yes',
                '--force-window=yes',
                '--osc=yes' # Ensure On Screen Controller is visible for individual control
            ]
            
            print(f"Launching: {' '.join(cmd)}")
            p = subprocess.Popen(cmd)
            processes.append(p)
            
        # Control Window
        control_window = Toplevel(root)
        control_window.title("MultiMPV Controller")
        control_window.geometry("500x250")
        control_window.attributes("-topmost", True)
        
        def cleanup():
            for p in processes:
                if p.poll() is None:
                    p.terminate()
            root.quit()
            
        control_window.protocol("WM_DELETE_WINDOW", cleanup)
        atexit.register(cleanup)
        
        def send_command_all(cmd_str):
            for pipe_name in pipes:
                try:
                    fd = os.open(pipe_name, os.O_RDWR)
                    try:
                        os.write(fd, (cmd_str + '\n').encode('utf-8'))
                    finally:
                        os.close(fd)
                except Exception as e:
                    print(f"Failed to send to {pipe_name}: {e}")

        # Target Selection
        target_var = IntVar(master=control_window, value=-1) # -1 = All, 0..N = Specific Index
        
        def send_target_command(cmd_str):
            target = target_var.get()
            targets = []
            if target == -1:
                targets = pipes
            elif 0 <= target < len(pipes):
                targets = [pipes[target]]
            
            print(f"DEBUG: sending '{cmd_str}' to target {target} (pipes: {len(targets)})")
            
            for pipe_name in targets:
                try:
                    fd = os.open(pipe_name, os.O_RDWR)
                    try:
                        os.write(fd, (cmd_str + '\n').encode('utf-8'))
                    finally:
                        os.close(fd)
                except Exception as e:
                    print(f"Failed to send to {pipe_name}: {e}")

        frame = Frame(control_window)
        frame.pack(expand=True, fill='both', padx=10, pady=10)

        # Target UI
        target_frame = Frame(frame)
        target_frame.pack(pady=5)
        Label(target_frame, text="Target:").pack(side='left')
        Radiobutton(target_frame, text="All", variable=target_var, value=-1).pack(side='left')
        for i in range(len(vids)):
            Radiobutton(target_frame, text=f"{i+1}", variable=target_var, value=i).pack(side='left')

        Label(frame, text="Global Controls").pack(pady=5)
        
        btn_frame = Frame(frame)
        btn_frame.pack()
        
        Button(btn_frame, text="Play All", command=lambda: send_command_all("set pause no")).pack(side='left', padx=5)
        Button(btn_frame, text="Pause All", command=lambda: send_command_all("set pause yes")).pack(side='left', padx=5)
        Button(btn_frame, text="<< 10s", command=lambda: send_command_all("seek -10")).pack(side='left', padx=5)
        Button(btn_frame, text="10s >>", command=lambda: send_command_all("seek 10")).pack(side='left', padx=5)

        # Extra Controls
        extra_frame = Frame(frame)
        extra_frame.pack(pady=10)

        self.fine_seek_active = False
        
        def export_times():
            # Step 1: Tell each MPV to write to its own temp file to avoid file locking conflicts
            for i, pipe_name in enumerate(pipes):
                temp_file = f"exported_times_{i}.txt"
                # using > to overwrite any previous temp file
                cmd = f'run "cmd.exe" "/c" "echo ${{filename}}=${{time-pos}} > {temp_file}"'
                
                try:
                    fd = os.open(pipe_name, os.O_RDWR)
                    try:
                        os.write(fd, (cmd + '\n').encode('utf-8'))
                    finally:
                        os.close(fd)
                except Exception as e:
                    print(f"Failed to send export command to {pipe_name}: {e}")
            
            # Step 2: Wait briefly for IO
            time.sleep(0.5)
            
            # Step 3: Collect and Merge
            collected_times = []
            for i in range(len(pipes)):
                temp_file = f"exported_times_{i}.txt"
                if os.path.exists(temp_file):
                    try:
                        with open(temp_file, 'r') as f:
                            content = f.read().strip()
                            if content:
                                collected_times.append(content)
                        os.remove(temp_file)
                    except Exception as e:
                        print(f"Error reading/removing {temp_file}: {e}")
            
            if collected_times:
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                with open("exported_times.txt", "a") as master_file:
                    master_file.write(f"\n--- Exported at {timestamp} ---\n")
                    for entry in collected_times:
                        master_file.write(entry + "\n")
                print(f"Exported {len(collected_times)} timestamps.")
                send_command_all(f'show-text "Exported {len(collected_times)} times"')
            else:
                print("No times collected.")
                send_command_all('show-text "Export Failed"')

        Button(extra_frame, text="Export Times", command=export_times).pack(side='left', padx=5)

        # Fine Seek Buttons (Targeted)
        fine_controls = Frame(frame)
        fine_controls.pack(pady=5)
        Button(fine_controls, text="<< 0.1s", command=lambda: send_target_command("seek -0.1 relative+exact")).pack(side='left', padx=5)
        Button(fine_controls, text="0.1s >>", command=lambda: send_target_command("seek 0.1 relative+exact")).pack(side='left', padx=5)

        
        root.mainloop()

    def run(self, txt_file=None):
        Tk().withdraw()  # keep the root window from appearing
        mpv_path = self._assert_mpv_exe()
        if not txt_file:
            vids = self.get_vids()
        else:
            vids = self.get_vids_from_txt(txt_file)

        if not vids:
            sys.exit()

        self.assert_vids_location(vids)
        self.run_independent(vids, mpv_path)


if __name__ == '__main__':
    cwd = sys.path[0]
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
