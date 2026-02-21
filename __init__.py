import bpy
import math
import time
from mathutils import Vector
from bpy_extras import view3d_utils
from bpy.props import FloatProperty, BoolProperty
from bpy.types import AddonPreferences

class SLCameraPreferences(AddonPreferences):
    bl_idname = __package__
    
    pan_sensitivity: FloatProperty(
        name="Pan Sensitivity",
        description="Camera panning sensitivity",
        default=0.001,
        min=0.0001,
        max=0.05,
        precision=3
    )
    
    zoom_sensitivity: FloatProperty(
        name="Zoom Sensitivity",
        description="Zoom in/out sensitivity",
        default=0.04,
        min=0.0001,
        max=0.1,
        precision=3
    )
    
    orbit_sensitivity: FloatProperty(
        name="Orbit Sensitivity",
        description="Orbit rotation sensitivity",
        default=0.004,
        min=0.0001,
        max=0.05,
        precision=3
    )
    
    invert_horizontal: BoolProperty(
        name="Invert Horizontal",
        description="Invert horizontal camera movement (unchecked = natural inverted behavior)",
        default=False
    )
    
    invert_vertical: BoolProperty(
        name="Invert Vertical",
        description="Invert vertical camera movement (unchecked = natural inverted behavior, does not affect zoom)",
        default=False
    )

    min_zoom_distance: FloatProperty(
        name="Min Zoom Distance",
        description="Closest the camera can get to the focus point",
        default=0.01,
        min=0.0001,
        max=5.0,
        precision=2
    )
    
    max_zoom_distance: FloatProperty(
        name="Max Zoom Distance",
        description="Farthest the camera can get from the focus point",
        default=200.0,
        min=10.0,
        max=1000.0,
        precision=1
    )
    
    orbit_elevation_limit: FloatProperty(
        name="Orbit Elevation Limit",
        description="Maximum vertical angle for orbit, in degrees (e.g., 85 is almost straight down)",
        default=85.0,
        min=45.0,
        max=89.0,
        precision=1
    )
    
    def draw(self, context):
        layout = self.layout
        
        col = layout.column()
        col.label(text="Sensitivity Settings:")
        col.prop(self, "pan_sensitivity")
        col.prop(self, "zoom_sensitivity")
        col.prop(self, "orbit_sensitivity")
        
        col.separator()
        col.label(text="Axis Inversion:")
        col.prop(self, "invert_horizontal")
        col.prop(self, "invert_vertical")
        
        col.separator()
        col.label(text="Camera Limits:")
        col.prop(self, "min_zoom_distance")
        col.prop(self, "max_zoom_distance")
        col.prop(self, "orbit_elevation_limit")

class SL_CAMERA_OT_modal(bpy.types.Operator):
    bl_idname = "view3d.sl_camera_modal"
    bl_label = "SL-Style Camera Control"
    bl_options = {'REGISTER', 'GRAB_CURSOR', 'BLOCKING'}
    
    # Operator property to determine which mode to use
    camera_mode: bpy.props.EnumProperty(
        name="Camera Mode",
        items=[
            ('FOCUS', "Focus", "Focus and pan around object"),
            ('ORBIT', "Orbit", "Orbit around object at fixed distance"),
            ('PAN', "Pan", "Pan across the scene"),
        ],
        default='FOCUS'
    )
    
    # Spherical coordinate state
    target_point = None  # Vector - the point we're looking at/orbiting around
    phi = math.pi / 4    # float - vertical angle from Y-axis (polar angle)
    theta = math.pi / 4  # float - horizontal angle in X-Z plane (azimuthal angle)
    distance = 14.0      # float - distance from target point

    # Transition state
    is_transitioning = False
    start_rotation = None
    end_rotation = None
    transition_start_time = 0
    transition_duration = 150  # milliseconds
    start_target = None
    end_target = None
    initial_cam_pos = None
    
    # Timer for animation
    _timer = None

    def _apply_camera_transform(self, context, position, rotation):
        """Update the scene camera's world transform (used in camera-lock mode)."""
        camera = context.scene.camera
        if not camera:
            return
        mat = rotation.to_matrix().to_4x4()
        mat.translation = position
        camera.matrix_world = mat

    def _get_camera_matrix(self, context):
        """Return the camera-to-world matrix for the current view."""
        if self.camera_lock_mode and context.scene.camera:
            return context.scene.camera.matrix_world.copy()
        return context.space_data.region_3d.view_matrix.inverted()

    def _direction_to_spherical(self, direction, default_theta=None, default_phi=None):
        """Update spherical angles from a direction vector."""
        if direction.length > 0:
            normalized = direction.normalized()
            self.theta = math.atan2(normalized.y, normalized.x)
            self.phi = math.acos(max(-1, min(1, normalized.z)))
            return
        if default_theta is not None:
            self.theta = default_theta
        if default_phi is not None:
            self.phi = default_phi

    def _update_camera_position(self, context):
        """Convert spherical coordinates to camera position and update the view."""
        if not self.target_point:
            return
        
        # Convert spherical coordinates to Cartesian for Z-up system
        x = self.target_point.x + self.distance * math.sin(self.phi) * math.cos(self.theta)
        y = self.target_point.y + self.distance * math.sin(self.phi) * math.sin(self.theta)
        z = self.target_point.z + self.distance * math.cos(self.phi)
        
        camera_pos = Vector((x, y, z))
        
        # Calculate rotation to look at target
        direction = (self.target_point - camera_pos).normalized()
        look_at_rotation = direction.to_track_quat('-Z', 'Y')
        
        if self.camera_lock_mode:
            self._apply_camera_transform(context, camera_pos, look_at_rotation)
        else:
            rv3d = context.space_data.region_3d
            rv3d.view_location = self.target_point
            rv3d.view_rotation = look_at_rotation
            rv3d.view_distance = self.distance

    def invoke(self, context, event):
        if context.area.type != 'VIEW_3D':
            return {'CANCELLED'}
            
        # Use the mode from the property (set by keymap)
        self.mode = self.camera_mode

        # CLICK_DRAG: mouse is held, user is already dragging.
        # CLICK: mouse was pressed and released without dragging.
        is_drag = (event.value == 'CLICK_DRAG')
        self.mouse_down = is_drag

        if is_drag:
            # Use the original press position for raycast accuracy
            region = context.region
            self.last_x = event.mouse_prev_press_x - region.x
            self.last_y = event.mouse_prev_press_y - region.y
        else:
            self.last_x = event.mouse_region_x
            self.last_y = event.mouse_region_y
        
        # Initialize spherical coordinates from current view
        rv3d = context.space_data.region_3d
        
        self.camera_lock_mode = (
            rv3d.view_perspective == 'CAMERA' and
            context.space_data.lock_camera and
            context.scene.camera is not None
        )
        
        self.target_point = rv3d.view_location.copy()
        self.distance = rv3d.view_distance
        
        # Calculate current phi and theta from camera position.
        view_mat = self._get_camera_matrix(context)
        cam_pos = view_mat.translation.copy()
        direction = cam_pos - self.target_point
        self._direction_to_spherical(
            direction,
            default_theta=math.pi / 4,
            default_phi=math.pi / 4,
        )
        
        # Transition state
        self.start_target = None
        self.end_target = None
        self.initial_cam_pos = None
        
        # Get addon preferences
        self.prefs = context.preferences.addons[__package__].preferences
        
        # Setup timer for animation
        self._timer = context.window_manager.event_timer_add(0.016, window=context.window)  # ~60fps
        
        # Handle the initial click based on mode and update status.
        # For CLICK_DRAG, raycast from the original press position.
        if is_drag:
            self._handle_click(context, event, coord_override=(self.last_x, self.last_y))
        else:
            self._handle_click(context, event)
        self._update_status_text(context)
            
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}
    
    def modal(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            return self.finish(context)
        
        # Handle timer for smooth interpolation
        if event.type == 'TIMER':
            if self.is_transitioning:
                self._update_transition(context)
                context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        # Exit conditions
        if (event.type in {'LEFT_ALT', 'RIGHT_ALT'} and event.value == 'RELEASE'):
            return self.finish(context)

        # Determine the desired mode from modifier keys
        desired_mode = None
        if event.alt and event.ctrl and event.shift:
            desired_mode = 'PAN'
        elif event.alt and event.ctrl:
            desired_mode = 'ORBIT'
        elif event.alt:
            desired_mode = 'FOCUS'

        # If the mode has changed, update the state
        if desired_mode and desired_mode != self.mode:
            self._set_mode(context, desired_mode)

        # --- Event Handling ---
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.mouse_down = True
            self.last_x = event.mouse_region_x
            self.last_y = event.mouse_region_y
            
            self._handle_click(context, event)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.mouse_down = False
            return {'RUNNING_MODAL'}

        if event.type == 'MOUSEMOVE':
            # Handle all mouse movement to correctly track virtual coordinates
            self._handle_mouse_move(context, event)
            return {'RUNNING_MODAL'}

        return {'RUNNING_MODAL'}
    
    def finish(self, context):
        # Clear header text
        context.workspace.status_text_set(None)
        # Remove timer
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        return {'CANCELLED'}
    
    def _start_transition(self, context, target_point):
        """Start smooth transition to look at target point."""
        rv3d = context.space_data.region_3d
        
        # If there's no previous target, use the current view location as the start
        if self.target_point is None:
            self.target_point = rv3d.view_location.copy()
            
        self.start_target = self.target_point.copy()

        # In camera-lock mode, read current state from the camera object directly
        # since rv3d may not reflect our prior writes to matrix_world.
        view_mat = self._get_camera_matrix(context)
        self.initial_cam_pos = view_mat.translation.copy()
        if self.camera_lock_mode and context.scene.camera:
            self.start_rotation = view_mat.to_quaternion()
        else:
            self.start_rotation = rv3d.view_rotation.copy()

        # Calculate desired final state
        direction = (target_point - self.initial_cam_pos).normalized()
        self.end_rotation = direction.to_track_quat('-Z', 'Y')
        self.end_target = target_point.copy()
        
        # Start transition timer
        self.is_transitioning = True
        self.transition_start_time = time.time() * 1000  # milliseconds
        self.target_point = target_point.copy()

    def _update_transition(self, context):
        """Update smooth camera transition by interpolating rotation and target point."""
        if not self.is_transitioning:
            return
            
        current_time = time.time() * 1000
        elapsed = current_time - self.transition_start_time
        progress = min(1.0, elapsed / self.transition_duration)
        
        # Smooth easing
        if progress < 0.5:
            eased = 2 * progress * progress
        else:
            eased = 1 - pow(-2 * progress + 2, 2) / 2
        
        # Interpolate rotation and target point
        current_rotation = self.start_rotation.slerp(self.end_rotation, eased)
        current_target = self.start_target.lerp(self.end_target, eased)
        
        # Update view to keep camera position fixed while rotating toward target
        if self.camera_lock_mode:
            self._apply_camera_transform(context, self.initial_cam_pos, current_rotation)
        else:
            rv3d = context.space_data.region_3d
            rv3d.view_rotation = current_rotation
            rv3d.view_location = current_target
            rv3d.view_distance = (self.initial_cam_pos - current_target).length
        
        # Check if transition is complete
        if progress >= 1.0:
            self.is_transitioning = False
            self.target_point = self.end_target.copy() # Lock in the final target
            
            # Recompute spherical coordinates from the final state
            direction = (self.initial_cam_pos - self.target_point)
            self.distance = direction.length
            self._direction_to_spherical(direction)
    
    def _perform_raycast(self, context, event, coord_override=None):
        """Helper method to perform raycast and return result and location.
        
        :param coord_override: Optional (x, y) region-relative coordinates to
            raycast from instead of the event's current mouse position.
        """
        region = context.region
        rv3d = context.space_data.region_3d
        
        if coord_override is not None:
            coord = (
                coord_override[0] % region.width,
                coord_override[1] % region.height
            )
        else:
            coord = (
                event.mouse_region_x % region.width,
                event.mouse_region_y % region.height
            )
        
        view_vec = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        
        result, location, normal, index, object, matrix = context.scene.ray_cast(
            context.view_layer.depsgraph, ray_origin, view_vec)
        
        return result, location

    def _handle_target_click(self, context, event, coord_override=None):
        """Handle click to focus/orbit on an object at exact hit point."""
        result, location = self._perform_raycast(context, event, coord_override)
        
        if result:
            self._start_transition(context, location)
            context.area.tag_redraw()
            return True
        else:
            self.target_point = None
            return False

    def _handle_pan_click(self, context, event, coord_override=None):
        """Handle ALT+CTRL+SHIFT+Click to set pan focus (does not clear target on miss)"""
        result, location = self._perform_raycast(context, event, coord_override)
        
        if result:
            self._start_transition(context, location)
            context.area.tag_redraw()
            return True
        return False

    def _handle_focus_drag(self, context, dx, dy):
        """Handle ALT+Drag - orbit horizontally and zoom with mouse movement"""
        if not self.target_point:
            return
            
        # Apply axis inversion - horizontal orbiting and vertical zooming
        theta_delta = dx if self.prefs.invert_horizontal else -dx
        zoom_delta = dy  # Zoom direction should not be affected by invert_vertical
        
        # Update theta (horizontal orbiting)
        self.theta += theta_delta * self.prefs.orbit_sensitivity
        
        # Update distance (zooming)
        if zoom_delta != 0:
            zoom_sensitivity = self.prefs.zoom_sensitivity
            
            # Apply smooth distance-based zoom compensation
            # Use a curve that provides fine control when close, normal control when far
            base_factor = self.distance * 0.1
            fine_control_factor = 0.01 + (base_factor - 0.01) * (self.distance / (self.distance + 0.5))
            distance_factor = max(0.01, fine_control_factor)
            
            zoom_change = -zoom_delta * zoom_sensitivity * distance_factor
            
            # Apply distance limits
            min_distance = self.prefs.min_zoom_distance
            max_distance = self.prefs.max_zoom_distance
            self.distance = max(min_distance, min(max_distance, self.distance + zoom_change))
        
        # Update camera position using spherical coordinates
        self._update_camera_position(context)
    
    def _handle_orbit_drag(self, context, dx, dy):
        """Handle ALT+CTRL+Drag - orbit at constant distance with elevation limits"""
        if not self.target_point:
            return
            
        # Apply axis inversion
        theta_delta = dx if self.prefs.invert_horizontal else -dx
        phi_delta = dy if self.prefs.invert_vertical else -dy
        
        # Update theta (horizontal rotation)
        self.theta += theta_delta * self.prefs.orbit_sensitivity
        
        # Update phi (vertical rotation) with elevation limits
        new_phi = self.phi - phi_delta * self.prefs.orbit_sensitivity
        # Clamp phi to prevent looking straight up or down, based on user preference
        min_phi_from_pole = math.radians(90.0 - self.prefs.orbit_elevation_limit)
        min_phi = min_phi_from_pole
        max_phi = math.pi - min_phi_from_pole
        self.phi = max(min_phi, min(max_phi, new_phi))
        
        # Update camera position using spherical coordinates
        self._update_camera_position(context)
    
    def _handle_pan_drag(self, context, dx, dy):
        """Handle ALT+CTRL+SHIFT+Drag - pan camera and target together"""
        # Apply axis inversion for pan mode
        pan_dx = dx if self.prefs.invert_horizontal else -dx
        pan_dy = dy if self.prefs.invert_vertical else -dy
        
        # Get camera's right and up vectors. In camera-lock mode, read from the
        # camera object since rv3d may not reflect our prior matrix_world writes.
        view_mat = self._get_camera_matrix(context)
        right_vec = view_mat.to_3x3() @ Vector((1, 0, 0))
        up_vec = view_mat.to_3x3() @ Vector((0, 1, 0))
        
        # Calculate pan sensitivity based on distance
        base_sensitivity = self.prefs.pan_sensitivity
        distance_factor = max(0.01, self.distance)
        sensitivity = base_sensitivity * distance_factor
        
        # Calculate pan vector
        pan_vec = (right_vec * pan_dx + up_vec * pan_dy) * sensitivity
        
        self.target_point += pan_vec
        if self.camera_lock_mode:
            camera = context.scene.camera
            if camera:
                mat = camera.matrix_world.copy()
                mat.translation += pan_vec
                camera.matrix_world = mat
        else:
            context.space_data.region_3d.view_location += pan_vec

    def _update_status_text(self, context):
        """Update status text based on current mode and target state."""
        if self.mode == 'FOCUS':
            context.workspace.status_text_set("SL Camera: FOCUS mode - Drag to orbit/zoom | Hold Ctrl for orbit-only | Hold Ctrl+Shift to pan")
        elif self.mode == 'ORBIT':
            context.workspace.status_text_set("SL Camera: ORBIT mode - Drag to orbit at fixed distance | Release Ctrl for focus/zoom | Hold Shift to pan")
        elif self.mode == 'PAN':
            context.workspace.status_text_set("SL Camera: PAN mode - Drag to pan view | Release Shift for orbit | Release Ctrl+Shift for focus/zoom")

    def _set_mode(self, context, new_mode):
        """Sets the camera mode and handles state transitions."""
        if new_mode == self.mode:
            return

        rv3d = context.space_data.region_3d
        self.mode = new_mode
        self.is_transitioning = False  # Stop any transitions on mode change

        if new_mode in ('ORBIT', 'PAN'):
            # Ensure we have a target point for orbit/pan modes
            if not self.target_point:
                self.target_point = rv3d.view_location.copy()
        
        # Update status text for the new mode
        self._update_status_text(context)

    def _handle_click(self, context, event, coord_override=None):
        """Handles the initial mouse click for any mode."""
        if self.mode == 'FOCUS':
            self._handle_target_click(context, event, coord_override)
        elif self.mode == 'ORBIT':
            self._handle_target_click(context, event, coord_override)
        elif self.mode == 'PAN':
            self._handle_pan_click(context, event, coord_override)
            
    def _handle_mouse_move(self, context, event):
        """
        Handles mouse drag operations.
        """
        dx = event.mouse_region_x - self.last_x
        dy = event.mouse_region_y - self.last_y

        # If mouse is down, perform the drag action
        if self.mouse_down and (dx != 0 or dy != 0):
            if not self.is_transitioning:
                if self.mode == 'FOCUS':
                    self._handle_focus_drag(context, dx, dy)
                elif self.mode == 'ORBIT':
                    self._handle_orbit_drag(context, dx, dy)
                elif self.mode == 'PAN':
                    self._handle_pan_drag(context, dx, dy)
                
                context.area.tag_redraw()

        # Always update the last position for the next delta calculation
        self.last_x = event.mouse_region_x
        self.last_y = event.mouse_region_y
            
# --- register --------------------------------------------------------------
def register():
    bpy.utils.register_class(SLCameraPreferences)
    bpy.utils.register_class(SL_CAMERA_OT_modal)

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')

        # ALT + CTRL + SHIFT = PAN mode
        kmi = km.keymap_items.new(
            'view3d.sl_camera_modal',
            type='LEFTMOUSE',
            value='CLICK_DRAG',
            alt=True, ctrl=True, shift=True
        )
        kmi.properties.camera_mode = 'PAN'
        addon_keymaps.append((km, kmi))

        kmi = km.keymap_items.new(
            'view3d.sl_camera_modal',
            type='LEFTMOUSE',
            value='CLICK',
            alt=True, ctrl=True, shift=True
        )
        kmi.properties.camera_mode = 'PAN'
        addon_keymaps.append((km, kmi))

        # ALT + CTRL = ORBIT mode
        kmi = km.keymap_items.new(
            'view3d.sl_camera_modal',
            type='LEFTMOUSE',
            value='CLICK_DRAG',
            alt=True, ctrl=True, shift=False
        )
        kmi.properties.camera_mode = 'ORBIT'
        addon_keymaps.append((km, kmi))

        kmi = km.keymap_items.new(
            'view3d.sl_camera_modal',
            type='LEFTMOUSE',
            value='CLICK',
            alt=True, ctrl=True, shift=False
        )
        kmi.properties.camera_mode = 'ORBIT'
        addon_keymaps.append((km, kmi))

        # ALT only = FOCUS mode
        kmi = km.keymap_items.new(
            'view3d.sl_camera_modal',
            type='LEFTMOUSE',
            value='CLICK_DRAG',
            alt=True, ctrl=False, shift=False
        )
        kmi.properties.camera_mode = 'FOCUS'
        addon_keymaps.append((km, kmi))

        kmi = km.keymap_items.new(
            'view3d.sl_camera_modal',
            type='LEFTMOUSE',
            value='CLICK',
            alt=True, ctrl=False, shift=False
        )
        kmi.properties.camera_mode = 'FOCUS'
        addon_keymaps.append((km, kmi))


def unregister():
    # Remove keymap entries
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
    
    bpy.utils.unregister_class(SL_CAMERA_OT_modal)
    bpy.utils.unregister_class(SLCameraPreferences)


# Store keymap items here to remove on unregister
addon_keymaps = []


if __name__ == "__main__":
    register()