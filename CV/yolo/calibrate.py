"""
Perspective Calibration Tool for Bird's Eye View Transformation

Interactive GUI tool to calibrate perspective transformation for converting
camera view to top-down (bird's eye) view. Useful for tracking people in
physical space coordinates.

USAGE:
    python calibrate.py --video <video_path>

CONTROLS:
    - Drag the 4 yellow corner points to align the green grid with the floor
    - 'w' key: Preview the bird's eye view transformation
    - 's' key: Save calibration and exit
    - 'q' or Esc: Exit without saving

OUTPUT FILES:
    calibration_matrix.npy   - 3x3 homography matrix (numpy format)
    calibration_points.json  - Original corner points and video source

HOW IT WORKS:
    1. Opens first frame of video
    2. User drags 4 corners to define a quadrilateral on the floor
    3. The quadrilateral is mapped to a square (bird's eye view)
    4. Homography matrix is computed using cv2.getPerspectiveTransform()

USING THE CALIBRATION:
    import numpy as np
    import cv2

    # Load the calibration matrix
    matrix = np.load('calibration_matrix.npy')

    # Transform a point from camera to bird's eye coordinates
    point = np.array([[[x, y]]], dtype=np.float32)
    transformed = cv2.perspectiveTransform(point, matrix)
    bird_eye_x, bird_eye_y = transformed[0][0]

    # Transform entire image
    warped = cv2.warpPerspective(frame, matrix, (1000, 1000))

REQUIREMENTS:
    pip install opencv-python numpy

NOTES:
    - Grid is 5x5 by default to help visualize perspective alignment
    - Large videos are automatically scaled down to fit screen (max 1280px width)
    - Calibration is saved in original video resolution (not scaled)
    - Target output is 1000x1000 pixels by default
"""

import cv2
import numpy as np
import argparse
import os


class PerspectiveCalibrator:
    def __init__(self, video_path, grid_rows=5, grid_cols=5):
        self.video_path = video_path
        self.cap = cv2.VideoCapture(video_path)

        # קריאת הפריים הראשון
        ret, self.frame = self.cap.read()
        if not ret:
            raise ValueError(f"Could not read video: {video_path}")

        # הקטנת הפריים אם הוא ענק (כדי שייכנס למסך)
        self.scale_factor = 1.0
        if self.frame.shape[1] > 1280:
            self.scale_factor = 1280 / self.frame.shape[1]
            self.frame = cv2.resize(self.frame, None, fx=self.scale_factor, fy=self.scale_factor)

        self.h, self.w = self.frame.shape[:2]

        # נקודות התחלתיות (טרפז במרכז המסך)
        margin_x = self.w // 4
        margin_y = self.h // 4
        self.points = np.array([
            [margin_x, margin_y],  # Top-Left
            [self.w - margin_x, margin_y],  # Top-Right
            [self.w - margin_x, self.h - margin_y],  # Bottom-Right
            [margin_x, self.h - margin_y]  # Bottom-Left
        ], dtype=np.float32)

        self.selected_point_idx = -1
        self.dragging = False
        self.grid_rows = grid_rows
        self.grid_cols = grid_cols

        # שם החלון
        self.window_name = "Calibration Tool - Drag Corners to Align Grid"

    def mouse_callback(self, event, x, y, flags, param):
        """ מטפל באירועי עכבר: לחיצה, גרירה, שחרור """
        # רדיוס רגישות לתפיסת נקודה
        radius = 20

        if event == cv2.EVENT_LBUTTONDOWN:
            # בדיקה האם לחצנו קרוב לאחת הנקודות
            distances = [np.linalg.norm(pt - (x, y)) for pt in self.points]
            min_dist = min(distances)
            if min_dist < radius:
                self.selected_point_idx = distances.index(min_dist)
                self.dragging = True

        elif event == cv2.EVENT_MOUSEMOVE:
            if self.dragging and self.selected_point_idx != -1:
                # עדכון מיקום הנקודה הגרורה
                self.points[self.selected_point_idx] = (x, y)

        elif event == cv2.EVENT_LBUTTONUP:
            self.dragging = False
            self.selected_point_idx = -1

    def draw_grid(self, img):
        """ מצייר את הגריד הפנימי כדי לעזור לעין לראות פרספקטיבה """
        overlay = img.copy()

        # סדר הנקודות: TL, TR, BR, BL
        tl, tr, br, bl = self.points

        # ציור המסגרת החיצונית
        pts = self.points.astype(int)
        cv2.polylines(overlay, [pts], isClosed=True, color=(0, 255, 0), thickness=2)

        # ציור קווי אורך ורוחב (אינטרפולציה)
        # קווים אנכיים
        for i in range(1, self.grid_cols):
            alpha = i / self.grid_cols
            top_pt = (1 - alpha) * tl + alpha * tr
            bot_pt = (1 - alpha) * bl + alpha * br
            cv2.line(overlay, tuple(top_pt.astype(int)), tuple(bot_pt.astype(int)), (0, 255, 0), 1)

        # קווים אופקיים
        for i in range(1, self.grid_rows):
            alpha = i / self.grid_rows
            left_pt = (1 - alpha) * tl + alpha * bl
            right_pt = (1 - alpha) * tr + alpha * br
            cv2.line(overlay, tuple(left_pt.astype(int)), tuple(right_pt.astype(int)), (0, 255, 0), 1)

        # ציור עיגולים בפינות (מודגש אם נבחר)
        for i, pt in enumerate(self.points):
            color = (0, 0, 255) if i == self.selected_point_idx else (0, 255, 255)
            cv2.circle(overlay, tuple(pt.astype(int)), 8, color, -1)

        # הוספת שקיפות (כדי שיראה כמו שכבה)
        alpha = 0.6
        cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0, img)

    def get_birds_eye_view(self):
        """ מחשב ומציג איך זה נראה במבט על (Preview) """
        # גודל היעד (מרובע שטוח)
        width, height = 500, 500
        dst_pts = np.float32([
            [0, 0],
            [width, 0],
            [width, height],
            [0, height]
        ])

        # חישוב המטריצה
        matrix = cv2.getPerspectiveTransform(self.points, dst_pts)

        # יצירת תמונת ה-Warped
        warped = cv2.warpPerspective(self.frame, matrix, (width, height))
        return warped, matrix

    def run(self):
        cv2.namedWindow(self.window_name)
        cv2.setMouseCallback(self.window_name, self.mouse_callback)

        print("--- הוראות שימוש ---")
        print("1. גרור את 4 הפינות הצהובות כדי ליישר את הגריד הירוק עם הרצפה.")
        print("2. לחץ 'w' כדי לראות תצוגה מקדימה של ה-Bird's Eye View.")
        print("3. לחץ 's' כדי לשמור את נתוני הכיול ולצאת.")
        print("4. לחץ 'q' או 'Esc' ליציאה ללא שמירה.")

        while True:
            display_img = self.frame.copy()
            self.draw_grid(display_img)

            # טקסט עזרה על המסך
            cv2.putText(display_img, "'s': Save | 'w': Preview | 'q': Quit", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            cv2.imshow(self.window_name, display_img)

            key = cv2.waitKey(10) & 0xFF

            if key == ord('w'):
                # הצגת תצוגה מקדימה
                warped, _ = self.get_birds_eye_view()
                cv2.imshow("Preview - Birds Eye View", warped)

            elif key == ord('s'):
                # שמירה
                self.save_calibration()
                break

            elif key == ord('q') or key == 27:
                break

        self.cap.release()
        cv2.destroyAllWindows()

    def save_calibration(self):
        """ שומר את המטריצה והנקודות לקובץ """
        # מחשבים שוב את המטריצה הסופית (ביחס לגודל המקורי, לא המוקטן!)
        original_points = self.points / self.scale_factor

        # נניח שאנחנו רוצים למפות את זה למטרים וירטואליים או פיקסלים
        # כאן אנחנו שומרים רק את ה-Homography Matrix
        # יעד: ריבוע נורמלי
        target_size = 1000  # פיקסלים
        dst_pts = np.float32([
            [0, 0],
            [target_size, 0],
            [target_size, target_size],
            [0, target_size]
        ])

        matrix = cv2.getPerspectiveTransform(original_points, dst_pts)

        output_file = "calibration_matrix.npy"
        np.save(output_file, matrix)

        # שמירת גם הנקודות הגולמיות לשימוש עתידי (JSON)
        import json
        points_list = original_points.tolist()
        with open("calibration_points.json", "w") as f:
            json.dump({"points": points_list, "video_source": self.video_path}, f)

        print(f"\n[SUCCESS] Calibration saved to '{output_file}' and 'calibration_points.json'")


if __name__ == "__main__":
    # אפשר להריץ משורת הפקודה עם נתיב לקובץ
    # python scripts/calibrate_perspective.py --video path/to/video.mp4
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=str, help="Path to video file", required=True)
    args = parser.parse_args()

    if not os.path.exists(args.video):
        print(f"Error: File {args.video} not found.")
    else:
        calibrator = PerspectiveCalibrator(args.video)
        calibrator.run()