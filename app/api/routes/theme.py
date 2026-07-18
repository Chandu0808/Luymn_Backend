# routes/theme.py
import os, random, shutil
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.models.theme_model import Theme
from app.models.user_model import User


router = APIRouter()

_THEME_EDITOR_ROLES = frozenset({"Admin", "Superadmin"})


def require_theme_editor(current_user: User = Depends(get_current_user)) -> User:
    """Restrict theme mutations to Superadmin and Admin."""
    if current_user.role not in _THEME_EDITOR_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action is restricted to Admin and Superadmin only.",
        )
    return current_user

@router.get("/")
def get_theme(request: Request, db: Session = Depends(get_db)):
    rows = db.query(Theme).all()
    data = {row.key: row.value for row in rows}

    base_url = str(request.base_url).rstrip("/")

    return {
        "status": "Success",
        "background_image": f"{base_url}{data.get('background_image', '')}",
        "ui_theme_colors": {
            "background": data.get("ui.background", ""),
            "content": data.get("ui.content", ""),
            "button": data.get("ui.button", "")
        },
        "heatmap_colors": {
            "light": data.get("heatmap.light", ""),
            "occupancy": data.get("heatmap.occupancy", ""),
            "energy": data.get("heatmap.energy", "")
        }
    }



@router.get("/background")
def get_background_image(request: Request, db: Session = Depends(get_db)):
    rows = db.query(Theme).all()
    data = {row.key: row.value for row in rows}

    base_url = str(request.base_url).rstrip("/")

    return {
        "status": "Success",
        "background_image": f"{base_url}{data.get('background_image', '')}"
    }

# Same folder mounted in main.py as /background_image (app/background_image)
UPLOAD_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "background_image")
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

DEFAULT_BACKGROUND_IMAGE = "/background_image/defaultBg.png"
THEME_BACKGROUND_KEY = "background_image"
_ALLOWED_BACKGROUND_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _background_label(filename: str) -> str:
    stem = os.path.splitext(filename)[0]
    if stem.startswith("bg_"):
        return "Uploaded"
    return stem.replace("_", " ").replace("-", " ").title()


@router.get("/background_images")
def list_background_images(request: Request, db: Session = Depends(get_db)):
    """List selectable background images from the static background_image folder."""
    base_url = str(request.base_url).rstrip("/")
    theme_row = db.query(Theme).filter(Theme.key == THEME_BACKGROUND_KEY).first()
    selected_path = theme_row.value if theme_row else DEFAULT_BACKGROUND_IMAGE

    images = []
    try:
        names = sorted(os.listdir(UPLOAD_DIR))
    except OSError:
        names = []

    for name in names:
        ext = os.path.splitext(name)[1].lower()
        if ext not in _ALLOWED_BACKGROUND_EXTS:
            continue
        path = f"/background_image/{name}"
        images.append(
            {
                "id": name,
                "label": _background_label(name),
                "path": path,
                "url": f"{base_url}{path}",
                "selected": path == selected_path,
            }
        )

    return {
        "status": "Success",
        "selected": f"{base_url}{selected_path}" if selected_path else "",
        "selected_path": selected_path or "",
        "background_images": images,
    }


class BackgroundSelectRequest(BaseModel):
    path: str


@router.post("/background/select")
def select_background_image(
    update: BackgroundSelectRequest,
    request: Request,
    db: Session = Depends(get_db),
    _editor: User = Depends(require_theme_editor),
):
    """Select an existing background image by relative path (does not upload a new file)."""
    raw = (update.path or "").strip().replace("\\", "/")
    if raw.startswith("http://") or raw.startswith("https://"):
        # Allow absolute URLs that point at this app's /background_image/ mount
        marker = "/background_image/"
        idx = raw.find(marker)
        if idx < 0:
            raise HTTPException(status_code=400, detail="Invalid background image path.")
        raw = raw[idx:]

    if not raw.startswith("/background_image/"):
        raise HTTPException(status_code=400, detail="Background path must be under /background_image/.")

    filename = os.path.basename(raw)
    if not filename or filename in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid background image filename.")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_BACKGROUND_EXTS:
        raise HTTPException(status_code=400, detail="Unsupported image type.")

    absolute = os.path.join(UPLOAD_DIR, filename)
    if not os.path.isfile(absolute):
        raise HTTPException(status_code=404, detail="Background image not found.")

    relative_path = f"/background_image/{filename}"
    theme_row = db.query(Theme).filter(Theme.key == THEME_BACKGROUND_KEY).first()
    if theme_row:
        theme_row.value = relative_path
    else:
        theme_row = Theme(key=THEME_BACKGROUND_KEY, value=relative_path)
        db.add(theme_row)

    db.commit()
    db.refresh(theme_row)

    base_url = str(request.base_url).rstrip("/")
    return {
        "status": "Updated",
        "background_image": f"{base_url}{relative_path}",
        "path": relative_path,
    }


@router.post("/background_image_clear")
def clear_background_image(
    request: Request,
    db: Session = Depends(get_db),
    _editor: User = Depends(require_theme_editor),
):
    """Reset the application background image to the seeded default."""
    theme_row = db.query(Theme).filter(Theme.key == THEME_BACKGROUND_KEY).first()
    previous_value = theme_row.value if theme_row else None

    if theme_row:
        theme_row.value = DEFAULT_BACKGROUND_IMAGE
    else:
        theme_row = Theme(key=THEME_BACKGROUND_KEY, value=DEFAULT_BACKGROUND_IMAGE)
        db.add(theme_row)

    db.commit()
    db.refresh(theme_row)

    if previous_value and previous_value != DEFAULT_BACKGROUND_IMAGE:
        uploaded_name = os.path.basename(previous_value)
        if uploaded_name.startswith("bg_"):
            uploaded_path = os.path.join(UPLOAD_DIR, uploaded_name)
            if os.path.isfile(uploaded_path):
                try:
                    os.remove(uploaded_path)
                except OSError:
                    pass

    base_url = str(request.base_url).rstrip("/")
    return {
        "status": "Updated",
        "background_image": f"{base_url}{DEFAULT_BACKGROUND_IMAGE}",
    }


@router.post("/background")
async def update_background_image_with_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _editor: User = Depends(require_theme_editor),
):
    try:
        ext = file.filename.split('.')[-1]
        unique_filename = f"bg_{random.randint(1000, 9999)}.{ext}"
        save_path = os.path.join(UPLOAD_DIR, unique_filename)
        with open(save_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        relative_path = f"/background_image/{unique_filename}"
        theme_row = db.query(Theme).filter(Theme.key == "background_image").first()
        if not theme_row:
            theme_row = Theme(key="background_image", value=relative_path)
            db.add(theme_row)
        else:
            theme_row.value = relative_path
        db.commit()
        db.refresh(theme_row)

        return {"status": "Updated", "background_image": relative_path}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- GET: current application theme colors ---
@router.get("/application")
def get_application_theme(db: Session = Depends(get_db)):
    theme_keys = ['ui.background', 'ui.content', 'ui.button']
    rows = db.query(Theme).filter(Theme.key.in_(theme_keys)).all()
    data = {row.key: row.value for row in rows}

    return {
        "status": "Success",
        "application_theme": {
            "background": data.get("ui.background", ""),
            "content": data.get("ui.content", ""),
            "button": data.get("ui.button", "")
        }
    }

# --- GET: current heatmap theme colors ---
@router.get("/heatmap")
def get_application_theme(db: Session = Depends(get_db)):
    theme_keys = ['heatmap.light', 'heatmap.occupancy', 'heatmap.energy']
    rows = db.query(Theme).filter(Theme.key.in_(theme_keys)).all()
    data = {row.key: row.value for row in rows}

    return {
        "status": "Success",
        "application_theme": {
            "light": data.get("heatmap.light", ""),
            "occupancy": data.get("heatmap.occupancy", ""),
            "energy": data.get("heatmap.energy", "")
        }
    }


# --- POST: update theme color ---
class ApplicationThemeUpdateRequest(BaseModel):
    background: Optional[str] = None
    content: Optional[str] = None
    button: Optional[str] = None

@router.post("/application")
def update_application_theme_bulk(
    update: ApplicationThemeUpdateRequest,
    db: Session = Depends(get_db),
    _editor: User = Depends(require_theme_editor),
):
    update_map = {
        "ui.background": update.background,
        "ui.content": update.content,
        "ui.button": update.button
    }

    updated_items = []

    for key, value in update_map.items():
        if value is None:
            continue  # Skip if not provided

        theme_row = db.query(Theme).filter(Theme.key == key).first()
        if theme_row:
            theme_row.value = value
        else:
            theme_row = Theme(key=key, value=value)
            db.add(theme_row)

        updated_items.append({key: value})

    db.commit()

    return {
        "status": "Updated",
        "updated_fields": updated_items
    }



class HeatmapBulkUpdateRequest(BaseModel):
    light: Optional[str] = None
    occupancy: Optional[str] = None
    energy: Optional[str] = None


@router.post("/heatmap")
def update_heatmap_theme_bulk(
    update: HeatmapBulkUpdateRequest,
    db: Session = Depends(get_db),
    _editor: User = Depends(require_theme_editor),
):
    update_map = {
        "heatmap.light": update.light,
        "heatmap.occupancy": update.occupancy,
        "heatmap.energy": update.energy
    }

    updated_items = []

    for key, value in update_map.items():
        if value is None:
            continue  # skip if the field wasn't included in the request

        theme_row = db.query(Theme).filter(Theme.key == key).first()
        if theme_row:
            theme_row.value = value
        else:
            theme_row = Theme(key=key, value=value)
            db.add(theme_row)

        updated_items.append({key: value})

    db.commit()

    return {
        "status": "Updated",
        "updated_fields": updated_items
    }
