"""
Store Intelligence — Person Detection Module

AI-Assisted Decision: YOLOv8n (nano) chosen over larger variants because it provides
sufficient accuracy for person detection in retail CCTV while being fast enough for
real-time processing. The challenge values functional correctness over model size (§6.1).

Edit Rationale: Implements person detection using ultralytics YOLOv8. Detects persons
(COCO class 0), filters by confidence threshold, and returns bounding boxes with
confidence scores. Handles frame-level GPU/CPU fallback gracefully.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

# Try to import ultralytics; fall back gracefully
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False


@dataclass
class Detection:
    """A single person detection in a frame."""
    bbox: list[float]  # [x1, y1, x2, y2]
    confidence: float
    class_id: int = 0  # 0 = person in COCO
    center: tuple[float, float] = field(init=False)

    def __post_init__(self):
        x1, y1, x2, y2 = self.bbox
        self.center = ((x1 + x2) / 2, (y1 + y2) / 2)

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return max(0, x2 - x1) * max(0, y2 - y1)


class PersonDetector:
    """
    YOLOv8-based person detector.
    
    Falls back to a motion-based detection if YOLO is not available,
    ensuring the pipeline always produces output even without GPU.
    """

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        confidence_threshold: float = 0.3,
        device: Optional[str] = None,
    ):
        self.confidence_threshold = confidence_threshold
        self.model = None
        self.device = device or ("cuda" if self._has_cuda() else "cpu")

        if YOLO_AVAILABLE:
            try:
                self.model = YOLO(model_path)
                print(f"[detect] YOLOv8 model loaded on {self.device}")
            except Exception as e:
                print(f"[detect] Failed to load YOLO model: {e}. Using fallback.")
        else:
            print("[detect] ultralytics not available. Using motion-based fallback.")

        # For motion-based fallback
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=50, detectShadows=True
        )

    @staticmethod
    def _has_cuda() -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """
        Detect persons in a frame.
        Returns list of Detection objects with bbox and confidence.
        """
        if self.model is not None:
            return self._detect_yolo(frame)
        else:
            return self._detect_motion(frame)

    def _detect_yolo(self, frame: np.ndarray) -> list[Detection]:
        """Run YOLOv8 inference for person detection."""
        results = self.model(
            frame,
            conf=self.confidence_threshold,
            classes=[0],  # Person class only
            verbose=False,
            device=self.device,
        )

        detections = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                bbox = box.xyxy[0].cpu().numpy().tolist()
                conf = float(box.conf[0].cpu().numpy())
                cls = int(box.cls[0].cpu().numpy())

                if cls == 0 and conf >= self.confidence_threshold:
                    detections.append(
                        Detection(bbox=bbox, confidence=conf, class_id=cls)
                    )

        return detections

    def _detect_motion(self, frame: np.ndarray) -> list[Detection]:
        """
        Motion-based person detection fallback.
        Uses background subtraction to detect moving objects.
        Not as accurate as YOLO but ensures pipeline produces output.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        fg_mask = self._bg_subtractor.apply(gray)

        # Clean up noise
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        detections = []
        frame_h, frame_w = frame.shape[:2]
        min_area = (frame_h * frame_w) * 0.005  # Min 0.5% of frame

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area:
                continue

            x, y, w, h = cv2.boundingRect(contour)
            aspect_ratio = h / w if w > 0 else 0

            # Person-like aspect ratio filter (roughly upright)
            if 1.2 < aspect_ratio < 4.0:
                bbox = [float(x), float(y), float(x + w), float(y + h)]
                # Lower confidence for motion-based detection
                conf = min(0.6, area / (frame_h * frame_w * 0.05))
                detections.append(
                    Detection(bbox=bbox, confidence=conf, class_id=0)
                )

        return detections
