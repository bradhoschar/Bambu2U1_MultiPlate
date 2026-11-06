# ⚡ Bambu2U1 Multi-Plate Converter

> Convert **Bambu Studio / MakerWorld multi-plate 3MF files** into individual, 100% compatible **Snapmaker U1-ready** projects — while preserving multi-color painting, filament colors, auto-centering on the U1 build plate, and eliminating prime tower variable layer height conflicts.

Inspired by [bl2u1](https://bl2u1.nbn.cat/), but built specifically to support **multi-plate projects with 12+ colors** (such as multi-part collector statues, dioramas, and complex models).

---

## 🌟 Why Bambu2U1 Multi-Plate?

Original single-plate tools force you to pick only 4 filaments for the entire project, which destroys multi-plate models that use 8, 12, or 16 total colors across different plates.

| Feature | Original bl2u1 | **Bambu2U1 Multi-Plate** |
| :--- | :---: | :---: |
| **Single-plate files** | ✅ | ✅ |
| **Multi-plate projects (e.g. 11 plates)** | ❌ (1 plate only) | ✅ **Full multi-plate support** |
| **Projects with 4+ total colors** | ❌ (Truncates to 4) | ✅ **Intelligent per-plate mapping** |
| **Plate Auto-Centering on U1 Bed** | ❌ (Off-plate crash) | ✅ **Exact 270x270mm translation** |
| **Fix Prime Tower Layer Conflict** | ❌ (Slicer error) | ✅ **Automatic conflict stripping** |
| **Interactive Visual Web UI** | ✅ | ✅ **Modern dark UI + plate previews** |
| **CLI / Drag-and-Drop Batch Mode** | ❌ | ✅ **1-Click Windows Batch & Python CLI** |
| **Docker Support** | ✅ | ✅ **Full Docker & Compose support** |

---

## 🚀 Quick Start

### 1. Windows 1-Click Launch (No Setup Required)
* **Web UI**: Double-click `Start_Web_UI.bat` (opens `http://localhost:5000` in your browser).
* **Instant Convert**: Drag & drop any `.3mf` file directly onto `Convert_To_U1.bat`.

### 2. Run with Python
```bash
# Clone the repository
git clone https://github.com/your-username/bambu2u1-multiplate.git
cd bambu2u1-multiplate

# Install dependencies
pip install -r requirements.txt

# Start the Web UI
python app.py
```
Open your browser to `http://localhost:5000`.

### 3. Command Line Interface (CLI)
Convert multi-plate 3MF files directly in your terminal:
```bash
python converter.py "C:\path\to\Wolverine.3mf" -o "./output_folder"
```

### 4. Docker / Self-Hosting
Run as a Docker container:
```bash
docker-compose up -d
```
Or build directly:
```bash
docker build -t bambu2u1 .
docker run -d -p 5000:5000 --name bambu2u1 bambu2u1
```

---

## ⚙️ How It Works Under The Hood

1. **Plate Discovery & Parsing**: Reads `Metadata/model_settings.config` and `3D/3dmodel.model` to identify every build plate, part, coordinate, and color swatch.
2. **Per-Plate Filament Allocation**: Detects which specific colors are used on each individual plate (typically 1 to 4) and remaps them cleanly into Snapmaker U1 slots 1–4.
3. **Bed Translation & Centering**: In Bambu Studio, plates 2 through N are offset at coordinate grids (e.g. X = +340mm, Y = -320mm). Bambu2U1 computes the exact grid translation vector and centers each plate onto the Snapmaker U1's 270x270mm bed coordinate space.
4. **Prime Tower Cleanup**: Bambu Studio projects often bundle global variable layer height curves (`layer_heights_profile.txt`) that cause OrcaSlicer to throw `"The prime tower is only supported if all objects have the same variable layer height"`. Bambu2U1 strips incompatible profiles so the prime tower generates cleanly.
5. **Snapmaker U1 Machine Profile Injection**: Applies the official Snapmaker U1 machine definitions, printable volume (`0.5x1` to `270.5x271`), and PLA SnapSpeed settings.

---

## 📦 Project Structure

```text
├── app.py                   # Flask Web Server & REST API
├── converter.py             # Core multi-plate conversion engine
├── templates/
│   ├── index.html           # Modern dark-mode Web UI
│   ├── u1_template.3mf      # Official Snapmaker U1 base profile
│   └── u1_template_supports.3mf # Snapmaker U1 tree-support profile
├── Convert_To_U1.bat        # Windows drag-and-drop batch converter
├── Start_Web_UI.bat         # Windows web UI launcher
├── requirements.txt         # Python dependencies (Flask, gunicorn)
├── Dockerfile               # Production container image
├── docker-compose.yml       # Docker compose orchestration
├── LICENSE                  # MIT License
└── README.md                # Documentation
```

---

## 📄 License
MIT License. Free to use, modify, and distribute for the 3D printing community.
