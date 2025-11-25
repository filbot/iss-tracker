import planetmapper
import os
import datetime

print(f"PlanetMapper version: {planetmapper.__version__}")
print(f"Kernel path: {planetmapper.get_kernel_path()}")

try:
    # planetmapper.set_kernel_path(os.path.expanduser('~/spice_kernels'))
    print(f"PlanetMapper dir: {dir(planetmapper)}")
    
    # Try default path
    body = planetmapper.Body('earth', datetime.datetime.now())
    print("Body initialized successfully.")
except Exception as e:
    print(f"Error: {e}")
