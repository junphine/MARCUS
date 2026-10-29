import os
import uuid
import torch
from typing import Tuple, Dict, Union, Literal, Any
from pathlib import Path
import contextlib
import json
import sys
import traceback
from fastapi import HTTPException, status
from app.config import UPLOAD_DIR

# Create a directory for chemical structure images
IMAGES_DIR = os.path.join(UPLOAD_DIR, "chem_images")
os.makedirs(IMAGES_DIR, exist_ok=True)


_molscribe_model = None
_DETECTOR = None
_RECOGNIZER = None


def _get_detector():
    global _DETECTOR
    if _DETECTOR is None:
        # Ultralytics + MolScribe both print startup chatter to stdout.
        # stdout is our JSON channel — redirect any library noise to
        # stderr so the parent only ever reads JSON lines.
        with contextlib.redirect_stdout(sys.stderr):
            from ultralytics import YOLO
            from modelscope import snapshot_download
            ckpt = snapshot_download(
                repo_id="UniParser/MolDetv2",
                allow_file_pattern="moldet_v2_yolo11n_960_doc.pt",
            )
            _DETECTOR = YOLO(ckpt+os.sep+"moldet_v2_yolo11n_960_doc.pt")
    return _DETECTOR


def _get_recognizer():
    global _RECOGNIZER
    if _RECOGNIZER is None:
        with contextlib.redirect_stdout(sys.stderr):
            from molscribe import MolScribe
            from modelscope import snapshot_download
            ckpt = snapshot_download(
                repo_id="polyai/MolScribe",
                allow_file_pattern="swin_base_char_aux_1m.pth",
            )
            _RECOGNIZER = MolScribe(ckpt+os.sep+"swin_base_char_aux_1m.pth", device=None)
    return _RECOGNIZER


def get_molnextr_prediction(
    path: Union[str, Path],
    data_type: Literal["smiles", "predicted_molfile", "both"] = "smiles",
) -> Union[Tuple[str, str], Tuple[str, str, str]]:
    """
    Get predictions from MolNexTR model for a given image path.

    Args:
        path: Path to the chemical structure image.
        data_type: Type of data to return. Options:
                   "smiles" - Return only SMILES string
                   "predicted_molfile" - Return only predicted molfile
                   "both" - Return both SMILES and predicted molfile

    Returns:
        If data_type is "smiles" or "predicted_molfile":
            A tuple containing (image_name, result_string)
        If data_type is "both":
            A tuple containing (image_name, smiles_string, molfile_string)

    Raises:
        FileNotFoundError: If the specified path does not exist.
        ValueError: If an invalid data_type is provided.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    image_name = path.name

    # Define prediction parameters based on data_type
    get_smiles = data_type in ("smiles", "both")
    get_molfile = data_type in ("predicted_molfile", "both")

    if data_type not in ("smiles", "predicted_molfile", "both"):
        raise ValueError(
            "Invalid data_type. Choose 'smiles', 'predicted_molfile', or 'both'."
        )

    # Use the singleton implementation to get predictions
    prediction = molnextr_get_predictions(
        str(path), atoms_bonds=False, smiles=get_smiles, predicted_molfile=get_molfile
    )

    # Return results based on data_type
    if data_type == "smiles":
        return image_name, prediction.get("predicted_smiles", "")
    elif data_type == "predicted_molfile":
        return image_name, prediction.get("predicted_molfile", "")
    else:  # data_type == "both"
        return (
            image_name,
            prediction.get("predicted_smiles", ""),
            prediction.get("predicted_molfile", ""),
        )



def init_molscribe(ckpt_path=None):
    """
    Initialize the MolScribe model.

    Args:
        ckpt_path: Path to checkpoint to use, if None then will use default

    Returns:
        MolScribe: Initialized MolScribe model instance
    """
    from molscribe import MolScribe
    global _molscribe_model

    if _molscribe_model is not None:
        return _molscribe_model

    try:
        if MolScribe is None:
            raise ImportError("MolScribe is not installed")

        _molscribe_model = _get_recognizer()
        if _molscribe_model:
            return _molscribe_model

        if ckpt_path is None:
            from huggingface_hub import hf_hub_download
            ckpt_path = hf_hub_download("yujieq/MolScribe", "swin_base_char_aux_1m.pth")

        _molscribe_model = MolScribe(ckpt_path, device=torch.device("cpu"))
        return _molscribe_model
    except Exception as e:
        raise RuntimeError(f"Failed to initialize MolScribe model: {str(e)}")


def get_molscribe_prediction(
    path: Union[str, Path], data_type: Literal["smiles", "molfile", "both"] = "both"
) -> Union[Tuple[str, str], Tuple[str, str, str]]:
    """
    Get predictions from MolScribe model for a given image path.

    Args:
        path: Path to the chemical structure image.
        data_type: Type of data to return. Options:
                   "smiles" - Return only SMILES string
                   "molfile" - Return only molfile
                   "both" - Return both SMILES and molfile

    Returns:
        If data_type is "smiles":
            A tuple containing (image_name, smiles_string)
        If data_type is "molfile":
            A tuple containing (image_name, molfile_string)
        If data_type is "both":
            A tuple containing (image_name, smiles_string, molfile_string)

    Raises:
        FileNotFoundError: If the specified path does not exist.
        ValueError: If an invalid data_type is provided.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    image_name = path.name

    # Initialize model if needed
    model = init_molscribe()

    # Get prediction
    try:
        prediction = model.predict_image_file(str(path))

        smiles = prediction.get("smiles", "")
        molfile = prediction.get("molfile", "")

        # Return results based on data_type
        if data_type == "smiles":
            return image_name, smiles
        elif data_type == "molfile":
            return image_name, molfile
        else:  # data_type == "both"
            return image_name, smiles, molfile
    except Exception as e:
        raise RuntimeError(f"Error in MolScribe prediction: {str(e)}")


def save_uploaded_image(file_content: bytes, filename: str) -> str:
    """
    Save an uploaded image to the images directory with a unique filename

    Args:
        file_content: The binary content of the uploaded file
        filename: Original filename

    Returns:
        str: Path to the saved image file
    """
    # Generate unique ID
    unique_id = str(uuid.uuid4())[:8]
    name, ext = os.path.splitext(filename)

    # Generate safe filename with unique ID
    safe_filename = f"{name}_{unique_id}{ext}"
    file_path = os.path.join(IMAGES_DIR, safe_filename)

    # Save the file
    with open(file_path, "wb") as f:
        f.write(file_content)

    return file_path


def process_chemical_structure(
    file_path: str,
    engine: Literal["molecode", "molnextr", "molscribe"] = "molecode",
    output_type: Literal["smiles", "molfile", "both"] = "smiles",
    hand_drawn: bool = False,
) -> Dict[str, Any]:
    """
    Process a chemical structure image and return the requested output

    Args:
        file_path: Path to the image file
        engine: Which OCSR engine to use
        output_type: What type of output to return
        hand_drawn: Whether to use the hand-drawn model (only for DECIMER)

    Returns:
        Dict: Results including image name, SMILES, and/or molfile
    """
    try:
        # Add support for MolScribe engine
        if engine == "molscribe":
            if output_type == "smiles":
                image_name, smiles = get_molscribe_prediction(
                    file_path, data_type="smiles"
                )
                return {
                    "image_name": image_name,
                    "smiles": smiles,
                    "engine": engine,
                    "hardware": f"CPU (PyTorch)",
                }
            elif output_type == "molfile":
                image_name, molfile = get_molscribe_prediction(
                    file_path, data_type="molfile"
                )
                return {
                    "image_name": image_name,
                    "molfile": molfile,
                    "engine": engine,
                    "hardware": f"CPU (PyTorch)",
                }
            else:  # output_type == "both"
                image_name, smiles, molfile = get_molscribe_prediction(
                    file_path, data_type="both"
                )
                return {
                    "image_name": image_name,
                    "smiles": smiles,
                    "molfile": molfile,
                    "engine": engine,
                    "hardware": f"CPU (PyTorch)",
                }

        # Original MolNexTR processing
        elif engine == "molnextr":
            # Import device info if available
            try:
                from MolNexTR import MolNexTRSingleton
                device, device_name = MolNexTRSingleton.get_device()
                hardware_info = device_name
            except:
                hardware_info = "Unknown"
            from MolNexTR import get_predictions as molnextr_get_predictions

            if output_type == "smiles":
                image_name, smiles = get_molnextr_prediction(
                    file_path, data_type="smiles"
                )
                return {
                    "image_name": image_name,
                    "smiles": smiles,
                    "engine": engine,
                    "hardware": hardware_info,
                }
            elif output_type == "molfile":
                image_name, molfile = get_molnextr_prediction(
                    file_path, data_type="predicted_molfile"
                )
                return {
                    "image_name": image_name,
                    "molfile": molfile,
                    "engine": engine,
                    "hardware": hardware_info,
                }
            else:  # output_type == "both"
                image_name, smiles, molfile = get_molnextr_prediction(
                    file_path, data_type="both"
                )
                return {
                    "image_name": image_name,
                    "smiles": smiles,
                    "molfile": molfile,
                    "engine": engine,
                    "hardware": hardware_info,
                }
        elif engine=='molecode':
            from .molecode_wrapper import extract_smiles
            result = extract_smiles(file_path)
            return {
                "image_name": result['image_name'],
                "smiles": result['smiles'],
                "molfile": result['graph'],
                "engine": engine,
                "hardware": 'cpu',
            }

        else:
            raise ValueError(
                f"Unsupported engine: {engine}. Choose 'molecode', 'molnextr', or 'molscribe'."
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing chemical structure: {str(e)}",
        )
