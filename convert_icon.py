from PIL import Image

img = Image.open("QCert.png").convert("RGBA")
img.save(
    "QCert.ico",
    format="ICO",
    sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
)
print("Done! QCert.ico created.")
