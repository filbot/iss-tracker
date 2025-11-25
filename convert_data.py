import json

def convert_geojson_to_python(input_path, output_path):
    with open(input_path, 'r') as f:
        data = json.load(f)
    
    polygons = []
    
    for feature in data['features']:
        geom = feature['geometry']
        if geom['type'] == 'Polygon':
            # GeoJSON is (lon, lat), we want (lat, lon)
            for ring in geom['coordinates']:
                poly = [(p[1], p[0]) for p in ring]
                polygons.append(poly)
        elif geom['type'] == 'MultiPolygon':
            for polygon in geom['coordinates']:
                for ring in polygon:
                    poly = [(p[1], p[0]) for p in ring]
                    polygons.append(poly)
                    
    # Write to python file
    with open(output_path, 'w') as f:
        f.write('"""Simplified land mass coordinates (lat, lon)."""\n\n')
        f.write('LAND_MASSES = [\n')
        for poly in polygons:
            # Simple optimization: skip very small islands to keep file size down
            if len(poly) < 10: 
                continue
                
            # Round to 2 decimal places to save space
            rounded_poly = [(round(p[0], 2), round(p[1], 2)) for p in poly]
            f.write(f'    {rounded_poly},\n')
        f.write(']\n')

if __name__ == "__main__":
    convert_geojson_to_python('var/ne_110m_land.geojson', 'src/iss_display/data/world_110m.py')
