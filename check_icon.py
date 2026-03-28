from PIL import Image
import os

def check_icon():
    icon_path = "icon.ico"
    if not os.path.exists(icon_path):
        print(f"[!] {icon_path} not found!")
        return

    try:
        img = Image.open(icon_path)
        # Check how many layers are actually in the ICO file
        frames = getattr(img, 'n_frames', 1)
        print(f"[*] Icon file layers found: {frames}")
        
        # Check the size of each layer
        for i in range(frames):
            img.seek(i)
            print(f"[*] Layer {i}: {img.size}")
            
    except Exception as e:
        print(f"[!] Could not read icon: {e}")

if __name__ == "__main__":
    check_icon()
