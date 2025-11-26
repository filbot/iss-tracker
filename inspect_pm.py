import planetmapper
import inspect

print("PlanetMapper Version:", planetmapper.__version__)
print("\nAttributes of planetmapper.kernel_downloader:")
for name, obj in inspect.getmembers(planetmapper.kernel_downloader):
    if not name.startswith("_"):
        print(f"- {name}")
