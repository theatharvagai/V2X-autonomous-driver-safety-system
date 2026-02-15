import pygame
import random
import time
import json
import os
import threading
import math
import cv2
import numpy as np
import paho.mqtt.client as mqtt

# ==========================================
# 1. CONFIGURATION - UNIFIED SYSTEM
# ==========================================
class Config:
    """Unified configuration for complete V2X system"""
    
    # Network Settings
    BROKER = "broker.emqx.io"
    PORT = 1883
    TOPIC_VEHICLE = "v2x/hackathon/unified/cars"
    TOPIC_EMERGENCY = "v2x/hackathon/unified/emergency"

    # Display Settings
    SCREEN_WIDTH = 1400
    SCREEN_HEIGHT = 900
    FPS = 60

    # Road Configuration
    LANE_HEIGHT = 120
    LANE_COUNT = 4  # 3 Main lanes + 1 Service lane
    SERVICE_LANE_INDEX = 3  # Bottom lane is service lane
    ROAD_Y = 50
    TOTAL_LENGTH = 2000  # Virtual road length in meters
    
    # Vehicle Physics
    MAX_SPEED_KMH = 250
    MAX_SPEED_MS = MAX_SPEED_KMH / 3.6  # Convert to m/s
    ACCEL = 10  # m/s²
    BRAKE = 25  # m/s²
    LANE_CHANGE_SPEED = 2.0  # Speed of lane change animation
    
    # Safety Parameters
    SAFE_DIST = 200  # Safe following distance (meters)
    CRITICAL_DIST = 80  # Critical distance for emergency braking
    AMBULANCE_DIST = 400  # Distance to detect ambulance behind
    GHOST_TIMEOUT = 3.0  # Remove disconnected vehicles after 3 seconds
    DROWSY_TIME_THRESHOLD = 2.0  # Time without face detection = drowsy
    
    # Color Palette (Professional Design)
    C_BG = (235, 240, 245)  # Soft Blue-Grey background
    C_ROAD = (60, 60, 70)  # Dark Asphalt
    C_LINE = (220, 220, 220)  # Lane markings
    C_LINE_YELLOW = (240, 200, 50)  # Service lane marking
    C_PANEL = (40, 44, 50)  # Dark control panel
    C_TEXT = (230, 230, 230)  # Light text
    C_WARNING = (255, 60, 60)  # Warning red
    C_SERVICE_TEXT = (100, 100, 110)  # Service lane text
    
    # Navigation Colors
    C_NAV_BG = (30, 30, 40)  # Navigation background
    C_NAV_FULL_BG = (20, 20, 30)  # Full map background
    C_ROUTE_ACTIVE = (0, 255, 100)  # Active route color
    C_ROUTE_BLOCKED = (200, 50, 50)  # Blocked route color
    C_ROUTE_IDLE = (100, 100, 120)  # Idle route color


# ==========================================
# 2. DROWSINESS DETECTION SYSTEM
# ==========================================
class DrowsinessDetector:
    """
    Real-time drowsiness detection using camera.
    Detects if driver's face is not visible for threshold time.
    """
    
    def __init__(self):
        self.cap = cv2.VideoCapture(0)
        self.drowsy = False
        self.no_face_start_time = None
        self.running = True
        self.current_frame = None
        self.lock = threading.Lock()
        
        # Load face detection cascade
        self.face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        
        # Start detection thread
        self.thread = threading.Thread(target=self._detection_loop, daemon=True)
        self.thread.start()
        print("✅ Drowsiness Detection System Active")

    def _detection_loop(self):
        """Main detection loop running in separate thread"""
        while self.running and self.cap.isOpened():
            success, frame = self.cap.read()
            if not success:
                continue

            # Convert to grayscale for detection
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self.face_cascade.detectMultiScale(gray, 1.1, 4)

            # Check if face is detected
            if len(faces) == 0:
                if self.no_face_start_time is None:
                    self.no_face_start_time = time.time()
                elif time.time() - self.no_face_start_time > Config.DROWSY_TIME_THRESHOLD:
                    self.drowsy = True
            else:
                self.no_face_start_time = None
                self.drowsy = False
            
            # Draw face rectangles
            for (x, y, w, h) in faces:
                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)

            # Prepare frame for Pygame display
            with self.lock:
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                self.current_frame = np.rot90(rgb_frame)

            time.sleep(0.05)

    def get_frame(self):
        """Get current camera frame as Pygame surface"""
        with self.lock:
            if self.current_frame is not None:
                surf = pygame.surfarray.make_surface(self.current_frame)
                return pygame.transform.flip(surf, True, False)
        return None

    def stop(self):
        """Stop detection and release camera"""
        self.running = False
        if self.cap:
            self.cap.release()


# ==========================================
# 3. NAVIGATION SYSTEM
# ==========================================
class NavigationSystem:
    """
    Google Maps style navigation with multiple routes,
    obstacle detection, and dynamic rerouting with ETA calculation.
    """
    
    def __init__(self):
        self.width = 340
        self.height = 240
        self.x = 20
        self.y = 20
        self.rect = pygame.Rect(self.x, self.y, self.width, self.height)
        
        # Fonts
        self.font_s = pygame.font.SysFont("Arial", 16)
        self.font_b = pygame.font.SysFont("Arial", 22, bold=True)
        self.font_xl = pygame.font.SysFont("Arial", 40, bold=True)
        
        # State
        self.is_full_screen = False
        self.total_dist = 5000  # Total route distance
        self.destination_dist = self.total_dist
        
        # Define route geometry
        start = (150, 700)
        end = (1250, 200)
        
        self.routes = {
            "main": {
                "points": [start, (700, 450), end],
                "status": "active",
                "name": "Main Route",
                "length": 1.0  # Relative length multiplier
            },
            "top": {
                "points": [start, (400, 350), (700, 180), (1000, 180), end],
                "status": "idle",
                "name": "North Route",
                "length": 1.4
            },
            "bottom": {
                "points": [start, (450, 750), (900, 680), end],
                "status": "idle",
                "name": "South Route",
                "length": 1.2
            }
        }
        
        self.current_route_key = "main"
        self.old_route_key = None
        self.obstacles = []

    def reset_obstacles(self):
        """Clear all obstacles and reset routes"""
        self.obstacles = []
        self.old_route_key = None
        for key in self.routes:
            self.routes[key]["status"] = "idle"
        self.select_best_route()

    def select_best_route(self):
        """Select the best available route (prioritizes main > bottom > top)"""
        new_key = None
        
        # Priority: main -> bottom -> top
        if self.routes["main"]["status"] != "blocked":
            new_key = "main"
        elif self.routes["bottom"]["status"] != "blocked":
            new_key = "bottom"
        elif self.routes["top"]["status"] != "blocked":
            new_key = "top"
        
        # Track route changes for ETA comparison
        if new_key != self.current_route_key:
            self.old_route_key = self.current_route_key
        
        self.current_route_key = new_key
        
        # Update all route statuses
        for key in self.routes:
            if self.routes[key]["status"] != "blocked":
                self.routes[key]["status"] = "active" if key == self.current_route_key else "idle"

    def handle_map_click(self, pos):
        """Handle clicks on the map to add obstacles"""
        if not self.is_full_screen:
            return
        
        click_radius = 20
        clicked_route = None
        
        # Check if click is near any route
        for key, route in self.routes.items():
            points = route["points"]
            for i in range(len(points) - 1):
                p1 = points[i]
                p2 = points[i+1]
                if self._dist_to_segment(pos, p1, p2) < click_radius:
                    clicked_route = key
                    break
            if clicked_route:
                break
        
        if clicked_route:
            self.obstacles.append(pos)
            self.routes[clicked_route]["status"] = "blocked"
            self.select_best_route()
            print(f"🚧 Obstacle added to {clicked_route} route! Rerouting...")

    def _dist_to_segment(self, p, v, w):
        """Calculate distance from point p to line segment vw"""
        px, py = p
        vx, vy = v
        wx, wy = w
        l2 = (wx - vx)**2 + (wy - vy)**2
        if l2 == 0:
            return math.hypot(px - vx, py - vy)
        t = ((px - vx) * (wx - vx) + (py - vy) * (wy - vy)) / l2
        t = max(0, min(1, t))
        proj_x = vx + t * (wx - vx)
        proj_y = vy + t * (wy - vy)
        return math.hypot(px - proj_x, py - proj_y)

    def update(self, dt, my_vehicle):
        """Update navigation state based on vehicle movement"""
        self.destination_dist -= my_vehicle.speed * dt
        if self.destination_dist <= 0:
            self.destination_dist = self.total_dist

    def toggle_view(self):
        """Toggle between mini and full screen map view"""
        self.is_full_screen = not self.is_full_screen

    def _get_point_on_path(self, points, progress):
        """Get position on path given progress (0.0 to 1.0)"""
        if not points:
            return (0, 0)
        
        # Calculate segment lengths
        total_len = 0
        segs = []
        for i in range(len(points)-1):
            d = math.hypot(points[i+1][0]-points[i][0], points[i+1][1]-points[i][1])
            segs.append(d)
            total_len += d
        
        if total_len == 0:
            return points[0]
        
        # Find position along path
        target = progress * total_len
        curr = 0
        for i, length in enumerate(segs):
            if curr + length >= target:
                prog = (target - curr) / length
                x = points[i][0] + (points[i+1][0] - points[i][0]) * prog
                y = points[i][1] + (points[i+1][1] - points[i][1]) * prog
                return (int(x), int(y))
            curr += length
        return points[-1]

    def draw_mini(self, screen):
        """Draw mini navigation view"""
        pygame.draw.rect(screen, Config.C_NAV_BG, self.rect, border_radius=15)
        pygame.draw.rect(screen, (255, 255, 255), self.rect, 2, border_radius=15)
        
        # Title
        title = self.font_b.render("V2X Navigation", True, (255, 255, 255))
        screen.blit(title, (self.x + 15, self.y + 10))
        
        # Current route status
        curr_route = self.routes[self.current_route_key] if self.current_route_key else None
        status_txt = "NO ROUTE" if not curr_route else "ACTIVE"
        col = (255, 0, 0) if not curr_route else (0, 255, 0)
        
        status = self.font_xl.render(status_txt, True, col)
        screen.blit(status, (self.x + 40, self.y + 90))
        
        if curr_route:
            route_name = self.font_s.render(f"Via: {curr_route['name']}", True, (200, 200, 200))
            screen.blit(route_name, (self.x + 20, self.y + 160))
            
            # ETA
            base_speed = 20
            curr_len_mult = curr_route["length"]
            eta_val = int((self.destination_dist * curr_len_mult) / base_speed)
            eta_text = self.font_s.render(f"ETA: {eta_val}s", True, (200, 200, 200))
            screen.blit(eta_text, (self.x + 20, self.y + 190))
        
        # Instruction to expand
        hint = pygame.font.SysFont("Arial", 14).render("(Click to expand)", True, (150, 150, 150))
        screen.blit(hint, (self.x + 20, self.y + 215))

    def draw_full(self, screen):
        """Draw full screen navigation view"""
        screen.fill(Config.C_NAV_FULL_BG)
        
        # Grid background
        for x in range(0, Config.SCREEN_WIDTH, 100):
            pygame.draw.line(screen, (40, 40, 50), (x, 0), (x, Config.SCREEN_HEIGHT))
        for y in range(0, Config.SCREEN_HEIGHT, 100):
            pygame.draw.line(screen, (40, 40, 50), (0, y), (Config.SCREEN_WIDTH, y))

        # Title
        title = self.font_xl.render("LIVE NAVIGATION - Click Route to Add Obstacle", True, (255, 255, 255))
        screen.blit(title, (50, 30))

        # 1. Draw all routes
        for key, route in self.routes.items():
            color = Config.C_ROUTE_IDLE
            width = 5
            if route["status"] == "active":
                color = Config.C_ROUTE_ACTIVE
                width = 12
            elif route["status"] == "blocked":
                color = Config.C_ROUTE_BLOCKED
                width = 5
            
            if len(route["points"]) >= 2:
                pygame.draw.lines(screen, color, False, route["points"], width)

        # 2. Draw obstacles
        for obs in self.obstacles:
            pygame.draw.circle(screen, (255, 0, 0), obs, 15)
            pygame.draw.line(screen, (255, 255, 255), (obs[0]-10, obs[1]-10), (obs[0]+10, obs[1]+10), 3)
            pygame.draw.line(screen, (255, 255, 255), (obs[0]+10, obs[1]-10), (obs[0]-10, obs[1]+10), 3)

        # 3. Draw current vehicle position on route
        if self.current_route_key:
            base_speed = 20
            curr_len_mult = self.routes[self.current_route_key]["length"]
            eta_val = int((self.destination_dist * curr_len_mult) / base_speed)
            
            prog = 1.0 - (self.destination_dist / self.total_dist)
            curr_pos = self._get_point_on_path(self.routes[self.current_route_key]["points"], max(0, min(1, prog)))
            pygame.draw.circle(screen, (50, 200, 255), curr_pos, 20)
            pygame.draw.circle(screen, (255, 255, 255), curr_pos, 25, 3)
            
            # ETA Box
            box_x, box_y = Config.SCREEN_WIDTH - 300, 50
            pygame.draw.rect(screen, (30, 30, 40), (box_x, box_y, 250, 150), border_radius=10)
            pygame.draw.rect(screen, (100, 100, 100), (box_x, box_y, 250, 150), 2, border_radius=10)
            
            eta_label = self.font_b.render("Current ETA:", True, (200, 200, 200))
            screen.blit(eta_label, (box_x + 20, box_y + 20))
            eta_time = self.font_xl.render(f"{eta_val} sec", True, (0, 255, 0))
            screen.blit(eta_time, (box_x + 20, box_y + 50))
            
            # Show time saved if rerouted
            if self.old_route_key and self.old_route_key != self.current_route_key:
                old_eta = eta_val + 120
                old_text = self.font_s.render(f"Was: {old_eta}s (Delayed)", True, (255, 100, 100))
                screen.blit(old_text, (box_x + 20, box_y + 100))
                saved = self.font_s.render(f"Saved: 2 min", True, (255, 255, 255))
                screen.blit(saved, (box_x + 20, box_y + 120))
        
        # 4. Start/End labels
        start = self.routes["main"]["points"][0]
        end = self.routes["main"]["points"][-1]
        start_label = self.font_b.render("Start", True, (200, 200, 200))
        screen.blit(start_label, (start[0] - 20, start[1] + 20))
        end_label = self.font_b.render("End", True, (200, 200, 200))
        screen.blit(end_label, (end[0], end[1] - 40))


# ==========================================
# 4. UI BUTTON CLASS
# ==========================================
class Button:
    """Interactive button with hover effects"""
    
    def __init__(self, x, y, w, h, text, color=(0, 100, 200), cb=None):
        self.rect = pygame.Rect(x, y, w, h)
        self.text = text
        self.color = color
        self.cb = cb
        self.hover = False

    def draw(self, screen, font):
        """Draw button with hover effect"""
        col = (min(self.color[0]+20, 255), min(self.color[1]+20, 255), min(self.color[2]+20, 255)) if self.hover else self.color
        pygame.draw.rect(screen, col, self.rect, border_radius=6)
        pygame.draw.rect(screen, (150, 150, 150), self.rect, 1, border_radius=6)
        txt = font.render(self.text, True, (255, 255, 255))
        screen.blit(txt, (self.rect.centerx - txt.get_width()//2, self.rect.centery - txt.get_height()//2))

    def check_click(self, pos):
        """Check if button was clicked"""
        if self.rect.collidepoint(pos) and self.cb:
            self.cb()


# ==========================================
# 5. VEHICLE CLASS
# ==========================================
class Vehicle:
    """
    Vehicle with complete V2X capabilities:
    - Collision avoidance
    - Ambulance detection and yielding
    - Drowsiness detection and service lane parking
    """
    
    def __init__(self, uid, is_emergency=False):
        self.id = uid
        self.is_emergency = is_emergency
        self.lane = 1 if not is_emergency else 0
        self.visual_lane = float(self.lane)  # For smooth lane change animation
        self.x = 0 if is_emergency else random.randint(0, 1500)
        self.speed = Config.MAX_SPEED_MS if is_emergency else random.randint(20, 30)
        self.user_target_speed = 25
        self.target_speed = self.speed
        self.color = (255, 215, 0) if is_emergency else (random.randint(50, 200), random.randint(50, 200), 255)
        self.warning_vehicle_ahead = False
        self.braking = False
        self.drowsy_alert = False
        self.last_update = time.time()
        self.lane_change_cooldown = 0

    def update_physics(self, dt, all_vehicles, is_me_drowsy=False):
        """Update vehicle physics and AI behavior"""
        dist_to_ahead = float('inf')
        speed_of_car_ahead = None
        ambulance_behind = False
        
        # Check all other vehicles
        for other in all_vehicles.values():
            if other.id == self.id:
                continue
            
            # Calculate relative distances (with wraparound)
            d_ahead = other.x - self.x
            d_behind = self.x - other.x
            if d_ahead < 0:
                d_ahead += Config.TOTAL_LENGTH
            if d_behind < 0:
                d_behind += Config.TOTAL_LENGTH

            # Check vehicles in same lane
            if other.lane == self.lane:
                if d_ahead < dist_to_ahead:
                    dist_to_ahead = d_ahead
                    speed_of_car_ahead = other.speed
                
                # Check for ambulance behind
                if other.is_emergency and d_behind < Config.AMBULANCE_DIST:
                    ambulance_behind = True

        # Reset states
        self.warning_vehicle_ahead = False
        self.braking = False
        self.lane_change_cooldown -= dt

        # === BEHAVIOR LOGIC ===
        
        # 1. DROWSINESS HANDLING - Move to service lane and stop
        if is_me_drowsy:
            self.drowsy_alert = True
            if self.lane != Config.SERVICE_LANE_INDEX:
                # Move towards service lane
                if self.lane_change_cooldown <= 0:
                    self.lane += 1
                    self.lane_change_cooldown = 1.0
            else:
                # In service lane - stop the vehicle
                self.target_speed = 0
                self.braking = True
        
        # 2. AMBULANCE YIELDING - Clear the way
        elif ambulance_behind and not self.is_emergency and self.lane_change_cooldown <= 0:
            if self.lane < Config.LANE_COUNT - 1:
                self.lane += 1
                self.lane_change_cooldown = 2.0
                print(f"[{self.id}] 🚑 Yielding to ambulance - Moving right")
            elif self.lane > 0:
                self.lane -= 1
                self.lane_change_cooldown = 2.0
                print(f"[{self.id}] 🚑 Yielding to ambulance - Moving left")
        
        # 3. AMBULANCE BEHAVIOR - Maximum speed
        elif self.is_emergency:
            self.target_speed = Config.MAX_SPEED_MS * 1.2
        
        # 4. COLLISION AVOIDANCE
        elif dist_to_ahead < Config.CRITICAL_DIST:
            # CRITICAL - Emergency brake
            self.target_speed = 0
            self.warning_vehicle_ahead = True
            self.braking = True
        elif dist_to_ahead < Config.SAFE_DIST:
            # SAFE DISTANCE - Match speed of car ahead
            if speed_of_car_ahead is not None:
                self.target_speed = speed_of_car_ahead * 0.8
            else:
                self.target_speed = 10
            self.braking = True
            if self.speed > (speed_of_car_ahead or 0):
                self.warning_vehicle_ahead = True
        else:
            # CLEAR ROAD - Use user target speed
            self.target_speed = self.user_target_speed
        
        # Apply acceleration/braking
        if self.speed < self.target_speed:
            self.speed += Config.ACCEL * dt
        elif self.speed > self.target_speed:
            self.speed -= Config.BRAKE * dt

        # Clamp speed
        self.speed = max(0, min(self.speed, Config.MAX_SPEED_MS * 1.5))
        
        # Update position
        self.x += self.speed * dt
        if self.x > Config.TOTAL_LENGTH:
            self.x = 0

    def update_visuals(self, dt):
        """Smooth lane change animation"""
        diff = self.lane - self.visual_lane
        if abs(diff) > 0.01:
            direction = 1 if diff > 0 else -1
            self.visual_lane += direction * Config.LANE_CHANGE_SPEED * dt
            if abs(self.lane - self.visual_lane) < 0.05:
                self.visual_lane = float(self.lane)
        else:
            self.visual_lane = float(self.lane)

    def to_json(self):
        """Serialize vehicle state for network transmission"""
        return json.dumps({
            "id": self.id,
            "lane": self.lane,
            "x": self.x,
            "spd": self.speed,
            "emb": self.is_emergency,
            "col": self.color,
            "drw": self.drowsy_alert,
            "brk": self.braking
        })

    @staticmethod
    def from_json(payload):
        """Deserialize vehicle state from network"""
        try:
            d = json.loads(payload)
            v = Vehicle(d['id'], d['emb'])
            v.lane = d['lane']
            v.x = d['x']
            v.speed = d['spd']
            v.color = tuple(d['col'])
            v.drowsy_alert = d.get('drw', False)
            v.braking = d.get('brk', False)
            v.visual_lane = float(v.lane)
            v.last_update = time.time()
            return v
        except:
            return None


# ==========================================
# 6. MAIN APPLICATION
# ==========================================
class V2XUnifiedApp:
    """
    Complete V2X System combining:
    - Drowsiness detection
    - Navigation and routing
    - Collision avoidance
    - Ambulance priority
    - Service lane parking
    """
    
    def __init__(self, uid):
        pygame.init()
        self.screen = pygame.display.set_mode((Config.SCREEN_WIDTH, Config.SCREEN_HEIGHT))
        pygame.display.set_caption(f"V2X Unified System - {uid}")
        self.clock = pygame.time.Clock()
        
        # Fonts
        try:
            self.font = pygame.font.SysFont("Verdana", 18)
            self.big_font = pygame.font.SysFont("Verdana", 28, bold=True)
            self.road_font = pygame.font.SysFont("Arial Narrow", 50, bold=True)
            self.alert_font = pygame.font.SysFont("Verdana", 36, bold=True)
        except:
            self.font = pygame.font.Font(None, 24)
            self.big_font = pygame.font.Font(None, 32)
            self.road_font = pygame.font.Font(None, 50)
            self.alert_font = pygame.font.Font(None, 36)

        # Initialize vehicle
        self.my_vehicle = Vehicle(uid)
        self.my_vehicle.color = (0, 200, 255)  # Blue for user's car
        self.vehicles = {uid: self.my_vehicle}
        self.i_own_ambulance = False
        
        # Load assets
        self.images = self.load_assets()
        
        # Drowsiness state
        self.manual_drowsy = False  # Manual override with 'X' key
        self.any_drowsy_detected = False
        
        # Initialize systems
        print("Initializing Drowsiness Detection...")
        self.detector = DrowsinessDetector()
        
        print("Initializing Navigation System...")
        self.nav_system = NavigationSystem()
        
        # UI Controls
        y = 780
        btn_w = 110
        self.buttons = [
            Button(50, y, btn_w, 40, "< Left", cb=lambda: self.change_lane(-1)),
            Button(170, y, btn_w, 40, "Right >", cb=lambda: self.change_lane(1)),
            Button(400, y, btn_w, 40, "Slower", color=(180, 60, 60), cb=lambda: self.chg_speed(-5)),
            Button(520, y, btn_w, 40, "Faster", color=(60, 180, 60), cb=lambda: self.chg_speed(5)),
            Button(800, y, 220, 50, "CALL AMBULANCE", color=(220, 40, 40), cb=self.spawn_ambulance_click)
        ]
        
        # Map control buttons
        self.btn_back = Button(Config.SCREEN_WIDTH - 220, Config.SCREEN_HEIGHT - 80, 200, 60, 
                                "Back to Drive", color=(100, 100, 100), cb=self.nav_system.toggle_view)
        self.btn_reset = Button(Config.SCREEN_WIDTH - 440, Config.SCREEN_HEIGHT - 80, 200, 60, 
                                 "Reset Routes", color=(50, 150, 200), cb=self.nav_system.reset_obstacles)
        
        # Network setup
        self.client = mqtt.Client(client_id=f"{uid}_{random.randint(1000, 9999)}", 
                                   callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_message = self.on_message
        self.client.connect(Config.BROKER, Config.PORT)
        self.client.subscribe(Config.TOPIC_VEHICLE)
        self.client.loop_start()
        
        print("✅ V2X Unified System Ready!")
        print("Controls:")
        print("  - Arrow buttons or mouse: Change lane & speed")
        print("  - Click navigation box: Expand map")
        print("  - Click routes on map: Add obstacles")
        print("  - X key: Toggle manual drowsiness mode")
        print("  - CALL AMBULANCE button: Spawn emergency vehicle")

    def load_assets(self):
        """Load vehicle images if available"""
        imgs = {}
        CAR_SIZE = (110, 55)
        try:
            if os.path.exists("assets/car_blue.png"):
                imgs['me'] = pygame.transform.scale(pygame.image.load("assets/car_blue.png"), CAR_SIZE)
                imgs['other'] = pygame.transform.scale(pygame.image.load("assets/car_red.png"), CAR_SIZE)
                imgs['amb'] = pygame.transform.scale(pygame.image.load("assets/ambulance.png"), CAR_SIZE)
        except:
            pass
        return imgs

    def change_lane(self, direction):
        """Change lane with bounds checking"""
        new_lane = self.my_vehicle.lane + direction
        if 0 <= new_lane < Config.LANE_COUNT:
            self.my_vehicle.lane = new_lane

    def chg_speed(self, delta):
        """Adjust target speed"""
        self.my_vehicle.user_target_speed += delta
        self.my_vehicle.user_target_speed = max(0, min(self.my_vehicle.user_target_speed, Config.MAX_SPEED_MS))

    def spawn_ambulance_click(self):
        """Spawn ambulance vehicle"""
        if "AMB-1" not in self.vehicles:
            amb = Vehicle("AMB-1", is_emergency=True)
            self.vehicles["AMB-1"] = amb
            self.i_own_ambulance = True
            print("🚨 Ambulance dispatched!")

    def on_message(self, client, userdata, msg):
        """Handle incoming V2X messages"""
        try:
            payload = msg.payload.decode()
            d = json.loads(payload)
            if d['id'] != self.my_vehicle.id:
                # Skip if I own the ambulance
                if d['id'] == "AMB-1" and self.i_own_ambulance:
                    return
                
                if d['id'] not in self.vehicles:
                    self.vehicles[d['id']] = Vehicle.from_json(payload)
                else:
                    v = self.vehicles[d['id']]
                    v.x = d['x']
                    v.lane = d['lane']
                    v.speed = d['spd']
                    v.is_emergency = d['emb']
                    v.drowsy_alert = d.get('drw', False)
                    v.braking = d.get('brk', False)
                    v.last_update = time.time()
        except:
            pass

    def cleanup_ghosts(self):
        """Remove disconnected vehicles"""
        now = time.time()
        ghosts = [vid for vid, v in self.vehicles.items()
                  if vid != self.my_vehicle.id
                  and (now - v.last_update > Config.GHOST_TIMEOUT)]
        if self.i_own_ambulance and "AMB-1" in ghosts:
            ghosts.remove("AMB-1")
        for vid in ghosts:
            del self.vehicles[vid]

    def draw_dashed_line(self, surface, color, start_pos, end_pos, width=1, dash_len=20):
        """Draw dashed line for lane markings"""
        x1, y1 = start_pos
        x2, y2 = end_pos
        for x in range(x1, x2, dash_len * 2):
            pygame.draw.line(surface, color, (x, y1), (min(x + dash_len, x2), y1), width)

    def draw_car(self, v):
        """Draw vehicle on screen"""
        x = (v.x / Config.TOTAL_LENGTH) * Config.SCREEN_WIDTH
        y = Config.ROAD_Y + (v.visual_lane * Config.LANE_HEIGHT) + 35
        
        # Draw car body
        drawn = False
        if self.images:
            if v.is_emergency and 'amb' in self.images:
                self.screen.blit(self.images['amb'], (x, y))
                drawn = True
            elif v.id == self.my_vehicle.id and 'me' in self.images:
                self.screen.blit(self.images['me'], (x, y))
                drawn = True
            elif 'other' in self.images:
                self.screen.blit(self.images['other'], (x, y))
                drawn = True

        if not drawn:
            col = v.color
            if v.is_emergency and (pygame.time.get_ticks() % 200 < 100):
                col = (255, 50, 50)  # Flashing red for ambulance
            pygame.draw.rect(self.screen, col, (x, y, 110, 55), border_radius=10)
            pygame.draw.rect(self.screen, (0, 0, 0), (x, y, 110, 55), 2, border_radius=10)
            pygame.draw.rect(self.screen, (20, 20, 40), (x+70, y+5, 30, 45), border_radius=6)
        
        # Brake lights
        brake_col = (255, 0, 0) if v.braking else (100, 0, 0)
        pygame.draw.circle(self.screen, brake_col, (int(x+5), int(y+12)), 6)
        pygame.draw.circle(self.screen, brake_col, (int(x+5), int(y+43)), 6)
        
        # Vehicle ID label
        txt = self.font.render(v.id, True, (255, 255, 255) if not drawn else (0, 0, 0))
        self.screen.blit(txt, (x, y-25))
        
        # Drowsy indicator
        if v.drowsy_alert:
            zzz = self.big_font.render("ZZZ", True, (255, 0, 255))
            self.screen.blit(zzz, (x + 30, y - 50))

    def draw_simulation_view(self):
        """Draw the main driving simulation view"""
        self.screen.fill(Config.C_BG)
        
        # Draw road
        road_h = Config.LANE_HEIGHT * Config.LANE_COUNT
        pygame.draw.rect(self.screen, Config.C_ROAD, (0, Config.ROAD_Y, Config.SCREEN_WIDTH, road_h))
        
        # Draw lane markings
        for i in range(Config.LANE_COUNT + 1):
            y = Config.ROAD_Y + i * Config.LANE_HEIGHT
            line_color = Config.C_LINE
            
            if i == Config.SERVICE_LANE_INDEX:
                # Service lane marker (yellow solid line)
                line_color = Config.C_LINE_YELLOW
                pygame.draw.line(self.screen, line_color, (0, y), (Config.SCREEN_WIDTH, y), 4)
            elif i == 0 or i == Config.LANE_COUNT:
                # Road edges (solid white)
                pygame.draw.line(self.screen, line_color, (0, y), (Config.SCREEN_WIDTH, y), 3)
            else:
                # Lane dividers (dashed white)
                self.draw_dashed_line(self.screen, line_color, (0, y), (Config.SCREEN_WIDTH, y), 3, 30)

        # Draw "SERVICE LANE" text on road
        svc_y = Config.ROAD_Y + (Config.SERVICE_LANE_INDEX * Config.LANE_HEIGHT) + 40
        svc_txt = self.road_font.render("SERVICE LANE", True, Config.C_SERVICE_TEXT)
        for x in range(200, Config.SCREEN_WIDTH, 600):
            self.screen.blit(svc_txt, (x, svc_y))

        # Draw all vehicles
        for v in self.vehicles.values():
            self.draw_car(v)

        # Bottom control panel
        pygame.draw.rect(self.screen, Config.C_PANEL, (0, 740, Config.SCREEN_WIDTH, 160))
        for b in self.buttons:
            b.draw(self.screen, self.font)
        
        # Speedometer
        spd_kmh = int(self.my_vehicle.speed * 3.6)
        speed_text = self.big_font.render(f"Speed: {spd_kmh} km/h", True, Config.C_TEXT)
        self.screen.blit(speed_text, (50, 850))

        # === ALERT DISPLAYS ===
        blink_on = pygame.time.get_ticks() % 600 < 300
        
        # 1. Braking alert (center)
        if self.my_vehicle.braking and blink_on:
            alert = self.alert_font.render("BRAKING", True, (255, 100, 100))
            self.screen.blit(alert, (Config.SCREEN_WIDTH//2 - alert.get_width()//2, 750))

        # 2. Proximity warning (bottom right box)
        if self.my_vehicle.warning_vehicle_ahead:
            box_rect = pygame.Rect(Config.SCREEN_WIDTH - 280, 750, 260, 60)
            pygame.draw.rect(self.screen, (120, 0, 0), box_rect, border_radius=10)
            pygame.draw.rect(self.screen, (255, 0, 0), box_rect, 2, border_radius=10)
            txt = self.font.render("VEHICLE AHEAD!", True, (255, 255, 255))
            self.screen.blit(txt, (box_rect.x + 40, box_rect.y + 18))

        # 3. Global drowsiness alert (top center banner)
        if self.any_drowsy_detected and blink_on:
            warn_surf = self.alert_font.render("⚠️ DROWSY DRIVER DETECTED ⚠️", True, (255, 255, 255))
            pygame.draw.rect(self.screen, (220, 0, 0), 
                           (Config.SCREEN_WIDTH//2 - 420, 5, 840, 50), border_radius=10)
            self.screen.blit(warn_surf, (Config.SCREEN_WIDTH//2 - warn_surf.get_width()//2, 10))

        # === CAMERA FEED (top right) ===
        frame_surf = self.detector.get_frame()
        if frame_surf:
            frame_surf = pygame.transform.scale(frame_surf, (240, 180))
            cam_x = Config.SCREEN_WIDTH - 260
            cam_y = 60
            self.screen.blit(frame_surf, (cam_x, cam_y))
            pygame.draw.rect(self.screen, (0, 255, 255), (cam_x, cam_y, 240, 180), 2)
            cam_txt = self.font.render("DRIVER CAM", True, (0, 255, 255))
            self.screen.blit(cam_txt, (cam_x + 5, cam_y + 5))
            
            # Drowsy status indicator
            if self.detector.drowsy or self.manual_drowsy:
                status_txt = self.font.render("DROWSY!", True, (255, 0, 0))
                self.screen.blit(status_txt, (cam_x + 5, cam_y + 160))

        # === NAVIGATION MINI VIEW ===
        self.nav_system.draw_mini(self.screen)

    def run(self):
        """Main application loop"""
        running = True
        
        while running:
            dt = self.clock.tick(Config.FPS) / 1000.0
            mx, my = pygame.mouse.get_pos()
            
            # Event handling
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_x:
                        self.manual_drowsy = not self.manual_drowsy
                        print(f"Manual drowsiness mode: {'ON' if self.manual_drowsy else 'OFF'}")
                
                if event.type == pygame.MOUSEBUTTONDOWN:
                    if self.nav_system.is_full_screen:
                        # Map view controls
                        self.btn_back.check_click((mx, my))
                        self.btn_reset.check_click((mx, my))
                        self.nav_system.handle_map_click((mx, my))
                    else:
                        # Simulation view controls
                        for b in self.buttons:
                            b.check_click((mx, my))
                        if self.nav_system.rect.collidepoint((mx, my)):
                            self.nav_system.toggle_view()

            # Update button hover states
            for b in self.buttons:
                b.hover = b.rect.collidepoint((mx, my))
            self.btn_back.hover = self.btn_back.rect.collidepoint((mx, my))
            self.btn_reset.hover = self.btn_reset.rect.collidepoint((mx, my))

            # === UPDATE LOGIC ===
            
            # Check drowsiness (camera or manual override)
            is_drowsy = self.detector.drowsy or self.manual_drowsy
            
            # Update my vehicle
            self.my_vehicle.update_physics(dt, self.vehicles, is_me_drowsy=is_drowsy)
            
            # Check for any drowsy vehicles in the system
            self.any_drowsy_detected = is_drowsy
            for v in self.vehicles.values():
                if v.drowsy_alert:
                    self.any_drowsy_detected = True

            # Update ambulance if I own it
            if self.i_own_ambulance and "AMB-1" in self.vehicles:
                amb = self.vehicles["AMB-1"]
                amb.update_physics(dt, self.vehicles)
                self.client.publish(Config.TOPIC_VEHICLE, amb.to_json())

            # Update all vehicle visuals
            for v in self.vehicles.values():
                v.update_visuals(dt)
            
            # Broadcast my vehicle state
            self.client.publish(Config.TOPIC_VEHICLE, self.my_vehicle.to_json())
            
            # Update navigation system
            self.nav_system.update(dt, self.my_vehicle)
            
            # Cleanup disconnected vehicles
            self.cleanup_ghosts()

            # === RENDERING ===
            
            if self.nav_system.is_full_screen:
                # Full screen map view
                self.nav_system.draw_full(self.screen)
                self.btn_back.draw(self.screen, self.font)
                self.btn_reset.draw(self.screen, self.font)
            else:
                # Normal simulation view
                self.draw_simulation_view()

            pygame.display.flip()

        # Cleanup
        self.detector.stop()
        pygame.quit()
        self.client.loop_stop()
        print("✅ V2X System shut down successfully")


# ==========================================
# 7. MAIN ENTRY POINT
# ==========================================
if __name__ == "__main__":
    print("=" * 60)
    print("V2X UNIFIED SYSTEM - Complete Integration")
    print("=" * 60)
    print()
    print("Features:")
    print("  ✅ Drowsiness Detection (Camera)")
    print("  ✅ Service Lane Auto-Parking")
    print("  ✅ Collision Avoidance")
    print("  ✅ Ambulance Priority & Yielding")
    print("  ✅ Navigation with 3 Routes")
    print("  ✅ Dynamic Obstacle & Rerouting")
    print("  ✅ ETA Calculation")
    print("  ✅ V2X Communication (MQTT)")
    print()
    
    uid = input("Enter Vehicle ID (e.g., CAR1, CAR2): ").strip().upper() or "CAR1"
    
    app = V2XUnifiedApp(uid)
    app.run()
