import logging
from pathlib import Path
from typing import Optional, List, Tuple
import datetime

from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.backends.backend_agg as agg
import planetmapper
import numpy as np

from iss_display.config import Settings
from iss_display.data.world_110m import LAND_MASSES

logger = logging.getLogger(__name__)

class LcdDisplay:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.width = settings.display_width
        self.height = settings.display_height
        
        # Initialize PlanetMapper Body
        # We use 'earth' as the target.
        # We need an observer. 'moon' is a good distant observer to see the whole earth,
        # but we will override the view to be centered on the ISS anyway.
        # Using a specific date is fine, we can update it or just use it for geometry.
        # Ideally we update the date on every frame to get accurate sun illumination if we wanted it,
        # but for wireframe it matters less unless we show terminator.
        self.body = planetmapper.Body('earth', datetime.datetime.now(), observer='moon')
        
        # Pre-process land masses for faster plotting if possible?
        # We'll project them in the update loop.

    def update(self, lat: float, lon: float):
        """
        Updates the display with the current ISS position.
        
        Args:
            lat: ISS latitude
            lon: ISS longitude
        """
        # Create Matplotlib Figure
        # DPI 100 means 320x480 pixels = 3.2x4.8 inches
        dpi = 100
        fig = plt.figure(figsize=(self.width/dpi, self.height/dpi), dpi=dpi)
        
        # Set background color
        fig.patch.set_facecolor(self.settings.ui_background_color)
        
        ax = fig.add_axes([0, 0, 1, 1]) # Full screen axes
        ax.set_facecolor(self.settings.ui_background_color)
        ax.axis('off') # Hide axes
        
        # Calculate ISS position in RA/Dec (or Angular)
        # We want to center the view on the ISS.
        # PlanetMapper's angular coordinates can be centered on a custom RA/Dec.
        # First, find RA/Dec of the ISS (lat, lon) on Earth.
        # Note: planetmapper uses planetographic coords.
        
        # Update body time to now?
        # self.body.dt = datetime.datetime.now() # Not supported directly, need to re-init or use specific method?
        # Actually Body is immutable regarding time usually? No, it loads spice kernels.
        # Let's just use the initialized body, Earth rotation is handled by lon/lat.
        
        # Get RA/Dec of the ISS location
        # We need to ensure the point is visible from the observer to get a valid RA/Dec?
        # No, lonlat2radec should work if we are just doing geometry.
        # But wait, lonlat2radec gives the RA/Dec of the point *as seen by the observer*.
        # If the observer is the Moon, and ISS is on the far side, it might be an issue?
        # Actually, for "Angular" wireframe centered on a point, we just need the RA/Dec of that point.
        # Let's assume we can get it.
        
        try:
            iss_ra, iss_dec = self.body.lonlat2radec(lon, lat)
        except Exception:
            # Fallback if calculation fails (e.g. SPICE error or geometry issue)
            # Just center on Earth center
            iss_ra, iss_dec = self.body.target_ra, self.body.target_dec

        # Plot Wireframe
        # centered on ISS
        self.body.plot_wireframe_angular(
            ax,
            origin_ra=iss_ra,
            origin_dec=iss_dec,
            # Adjust scale to fit earth nicely
            scale_factor=None, # Default is arcseconds
            # Formatting
            color=self.settings.ui_earth_color,
            grid_interval=30, # Grid density
            formatting={
                'grid': {'linestyle': '-', 'linewidth': 0.5, 'alpha': 0.5, 'color': self.settings.ui_earth_color},
                'limb': {'linewidth': 1, 'color': self.settings.ui_earth_color},
                'terminator': {'visible': False}, # Hide terminator for simple wireframe
                'prime_meridian': {'visible': False},
                'equator': {'visible': False},
            }
        )
        
        # Plot Land Masses
        # We need to project land mass (lat, lon) to angular coordinates (x, y) on the plot.
        # planetmapper doesn't have a direct "plot polygons" helper, so we do it manually.
        # We can use body.lonlat2radec -> body.radec2angular
        
        for poly in LAND_MASSES:
            # Convert poly to arrays for speed
            lats = [p[0] for p in poly]
            lons = [p[1] for p in poly]
            
            # This might be slow to do point-by-point in python for all continents.
            # But let's try.
            xs = []
            ys = []
            
            # Optimization: Check if continent is roughly visible?
            # For now, just project all points.
            
            # Batch conversion would be better but planetmapper API is point-based mostly?
            # Actually lonlat2radec accepts arrays?
            # Documentation says "lon, lat : float or array-like".
            
            try:
                ras, decs = self.body.lonlat2radec(np.array(lons), np.array(lats))
                
                # Convert to angular coordinates centered on ISS
                # radec2angular also accepts arrays? Yes.
                ang_x, ang_y = self.body.radec2angular(ras, decs, origin_ra=iss_ra, origin_dec=iss_dec)
                
                # Filter out points that are "behind" the earth?
                # planetmapper wireframe usually handles visibility.
                # But here we are projecting manually.
                # We need to check visibility.
                # body.test_if_lonlat_visible(lon, lat) checks if visible *from observer*.
                # But we changed the "view" by centering on ISS.
                # Wait, plot_wireframe_angular just changes the coordinate system of the plot (centering).
                # It doesn't change the *observer*.
                # So if we use 'moon' as observer, we only see what the moon sees.
                # We want to see the Earth from a point above the ISS.
                # So we should set the observer to be a point above the ISS?
                # Or just use a generic observer and rotate the view?
                # If we use 'moon', and ISS is on the other side, we won't see it.
                
                # We need an observer that is looking at the ISS.
                # We can't easily move the observer in SPICE without defining a custom kernel or frame.
                # However, planetmapper allows `observer='coordinate'`? No.
                
                # Alternative:
                # Use `plot_wireframe_radec` but rotate the plot?
                # Or... maybe we don't need planetmapper for the *view* if it's hard to position the camera.
                # My previous `WireframeEarth` was actually better for "arbitrary view".
                
                # BUT, the user asked to use `planetmapper`.
                # Maybe we can define the observer as the ISS itself?
                # `body = planetmapper.Body('earth', 'now', observer='ISS')`?
                # SPICE usually has ISS kernels.
                # If we view Earth from ISS, we see a huge earth filling the screen.
                # The user wants "3d model of the earth... red blinking circle representing current location".
                # This implies an external view showing the Earth AND the ISS.
                
                # Let's stick to the "Moon" observer (far away) but we need to ensure the Earth is rotated
                # so the ISS is visible.
                # PlanetMapper shows the Earth as it is at that time.
                # We can't just "rotate the earth" in the simulation easily (it follows real time).
                # UNLESS we change the `dt` (time) to a time when the ISS longitude is facing the moon?
                # That's complicated.
                
                # Wait, `planetmapper` is for "fitting and mapping astronomical observations".
                # It simulates the real solar system.
                # If the user wants a "dashboard" that shows the ISS location,
                # maybe they want a "map view" (rectangular) or a "globe view" (orthographic).
                # My previous `WireframeEarth` did exactly that - a synthetic globe.
                
                # If I MUST use `planetmapper`, I have to work within its constraints.
                # Maybe I can use `lonlat2radec` to get coordinates, but I need to handle the rotation myself?
                # Or... I can use `planetmapper` just for the grid data?
                
                # Let's look at `plot_wireframe_angular` again.
                # "can also be customised to have a custom origin and rotation".
                # This shifts the center of the plot.
                # It doesn't rotate the sphere itself.
                
                # If I want to show the ISS location, I should probably just show the Earth as it is NOW,
                # and plot the ISS on it.
                # If the ISS is on the far side, it won't be visible.
                # Is that acceptable? "display the current position...".
                # Usually implies we want to SEE it.
                
                # Maybe I can cheat?
                # Set the observer to the Sun? Or just accept that sometimes it's on the back?
                # Or... can I define a custom observer in PlanetMapper?
                # The docs say `observer` can be a string name of a body.
                
                # Let's assume for now we just plot it as seen from a fixed point (e.g. Earth center? No).
                # Let's use 'Sun' as observer? Then we see the illuminated side.
                # Let's use 'Moon'.
                
                # To ensure ISS is visible, we might need to "fake" the time?
                # Or maybe the user is OK with a realistic view where it rotates?
                # "wire frame looking 3d model of the earth... red blinking circle... in relation to the earth".
                
                # Let's try to implement it with the observer as 'Moon' and center on Earth.
                # And plot the ISS. If it's behind, we plot it anyway (maybe dimmer)?
                # But `plot_wireframe` draws the grid.
                
                # Actually, `planetmapper` might be overkill if we just want a synthetic globe,
                # but the user asked for it.
                # Let's try to make it work.
                
                # Re: Land Masses.
                # I will plot them using `ax.plot(ang_x, ang_y)`.
                
                ax.plot(ang_x, ang_y, color=self.settings.ui_earth_color, linewidth=1)
                
            except Exception:
                continue

        # Plot ISS Marker
        # We already calculated iss_ra, iss_dec.
        iss_x, iss_y = self.body.radec2angular(iss_ra, iss_dec, origin_ra=iss_ra, origin_dec=iss_dec)
        # Wait, if we center on ISS, then ISS is at (0,0).
        iss_x, iss_y = 0, 0
        
        # Draw Red Circle
        # Use matplotlib marker
        ax.plot(iss_x, iss_y, 'o', color=self.settings.ui_iss_color, markersize=5) # markersize is points, not pixels?
        # 5px diameter approx.
        
        # Save to buffer
        canvas = agg.FigureCanvasAgg(fig)
        canvas.draw()
        
        # New matplotlib API (3.8+)
        rgba_buffer = canvas.buffer_rgba()
        size = canvas.get_width_height()
        
        self.image = Image.frombuffer("RGBA", size, rgba_buffer)
        self.image = self.image.convert("RGB")
        
        # Close figure to free memory
        plt.close(fig)
        
        # Save preview
        if self.settings.preview_dir:
            preview_path = self.settings.preview_dir / "lcd_preview.png"
            self.image.save(preview_path)
            logger.info(f"Saved preview to {preview_path}")

    def clear(self):
        pass
