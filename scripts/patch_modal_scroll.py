from pathlib import Path

index_path = Path(r"c:\daon\Daon agent System\index.html")
app_js_path = Path(r"c:\daon\Daon agent System\static\v2\js\app.js")

# 1. Update index.html
with open(index_path, "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace('max-h-[85vh]', 'max-h-[92vh]')
text = text.replace('app.js?v=20261009_0018', 'app.js?v=20261009_0025')

with open(index_path, "w", encoding="utf-8") as f:
    f.write(text)

# 2. Update app.js so add button scrolls form into view smoothly
with open(app_js_path, "r", encoding="utf-8") as f:
    code = f.read()

code = code.replace(
    """  if (addBtn) {
    addBtn.addEventListener('click', () => {
      resetForm();
      formCard.classList.remove('hidden');
    });
  }""",
    """  if (addBtn) {
    addBtn.addEventListener('click', () => {
      resetForm();
      formCard.classList.remove('hidden');
      setTimeout(() => formCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);
    });
  }"""
)

with open(app_js_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Modal scroll polish completed.")
