import os

BASE_PATH = r"/Users/abindal/Downloads/Skirt Tops/"  # change to your folder path

folders = [f for f in os.listdir(BASE_PATH) if os.path.isdir(os.path.join(BASE_PATH, f))]
print("('" + "', '".join(folders) + "')")
