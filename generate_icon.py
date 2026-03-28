from PIL import Image
import os

def create_icon():
    """Create a multi-layer professional Windows icon."""
    logo_path = "Logo.png"
    icon_path = "ffm_v3_logo.ico"
    
    if not os.path.exists(logo_path):
        print(f"[!] {logo_path} not found!")
        return

    print(f"[*] Bundling multi-layer HD icon...")
    
    try:
        img = Image.open(logo_path).convert('RGBA')
        
        # Ensure it's square
        w, h = img.size
        size = max(w, h)
        square_img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        square_img.paste(img, ((size - w) // 2, (size - h) // 2))

        # Windows Standard Sizes (Important for taskbar vs desktop)
        sizes = [256, 128, 64, 48, 32, 16]
        
        # Save them as a real multi-layer ICO
        square_img.save(icon_path, format='ICO', sizes=[(s, s) for s in sizes])
        
        # VERIFY
        actual_size = os.path.getsize(icon_path) / 1024
        print(f"[+] Final Icon Size: {actual_size:.2f} KB")
        
    except Exception as e:
        print(f"[!] Icon generation failed: {e}")

if __name__ == "__main__":
    create_icon()
