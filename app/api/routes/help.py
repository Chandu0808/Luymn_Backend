# routes/help_upload.py

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.models.help import HelpUpload
import shutil
import os
import uuid
from fastapi.responses import FileResponse
from app.models.user_model import User
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_operator_permission_for_scope


router = APIRouter()

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HELP_FILES_DIR = os.path.join(APP_DIR, "help_files")


def resolve_help_file_path(db_path: str) -> str:
    filename = os.path.basename(db_path.replace("\\", "/"))
    return os.path.join(HELP_FILES_DIR, filename)


def build_download_filename(record: HelpUpload) -> str:
    stored_name = os.path.basename(record.file_path.replace("\\", "/"))
    extension = os.path.splitext(stored_name)[1] or ".pdf"
    label = record.name.split("/")[-1].strip() or "help-file"
    safe_label = "".join(char if char.isalnum() or char in (" ", "-", "_") else "_" for char in label)
    return f"{safe_label}{extension}"


@router.post("/upload")
def upload_or_update_help_file(
    name: str = Form(...),  # e.g., "Project Information/Scope"
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # ---------- User-friendly Superadmin-only Permission Check ----------
    try:
        require_operator_permission_for_scope(
            required_level=5,   # only Superadmin can upload or update help files
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": "You don’t have permission to upload or update help files (Superadmin-only feature)."
            }
        if e.status_code == 422:
            return {
                "status": "failed",
                "message": "Unable to validate permission scope for this action."
            }
        raise  # re-raise unexpected errors

    # ---------- Define local relative path ----------
    os.makedirs(HELP_FILES_DIR, exist_ok=True)

    # Create a unique filename using UUID and original file extension
    file_ext = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(HELP_FILES_DIR, unique_filename)

    # Save the file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Save only relative path in DB (e.g., "/help_files/<filename>")
    db_file_path = f"/help_files/{unique_filename}"

    # Check if an entry with the same name exists
    existing = db.query(HelpUpload).filter(HelpUpload.name == name).first()

    if existing:
        existing.file_path = db_file_path
        db.commit()
        db.refresh(existing)
        return {
            "status": "updated",
            "message": "File updated successfully.",
            "data": {
                "id": existing.id,
                "name": existing.name,
                "file_path": existing.file_path
            }
        }
    else:
        new_entry = HelpUpload(name=name, file_path=db_file_path)
        db.add(new_entry)
        db.commit()
        db.refresh(new_entry)
        return {
            "status": "created",
            "message": "File uploaded successfully.",
            "data": {
                "id": new_entry.id,
                "name": new_entry.name,
                "file_path": new_entry.file_path
            }
        }


@router.get("/list")
def list_all_help_files(db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    records = db.query(HelpUpload).all()

    return [
        {
            "id": record.id,
            "name": record.name,
            "file_path": record.file_path
        }
        for record in records
    ]


@router.get("/download/{file_id}")
def download_help_file(
    file_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    record = db.query(HelpUpload).filter(HelpUpload.id == file_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Help file not found")

    absolute_path = resolve_help_file_path(record.file_path)
    if not os.path.isfile(absolute_path):
        raise HTTPException(status_code=404, detail="Help file not found on server")

    return FileResponse(
        absolute_path,
        media_type="application/octet-stream",
        filename=build_download_filename(record),
    )
