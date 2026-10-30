import os
import uuid
import torch
from typing import Tuple, Dict, Union, Literal, Any
from pathlib import Path
import contextlib
import json
import sys
from fastapi import HTTPException, status
from app.config import UPLOAD_DIR

# Create a directory for chemical structure images
IMAGES_DIR = os.path.join(UPLOAD_DIR, "chem_images")
os.makedirs(IMAGES_DIR, exist_ok=True)


_molparser_model = None
_DETECTOR = None


def _get_detector():
    global _DETECTOR
    if _DETECTOR is None:
        # Ultralytics prints startup chatter to stdout.
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




def init_molparser():
    """
    Initialize the MolParser model (singleton).

    Returns:
        MolParser: Initialized MolParser instance
    """
    global _molparser_model

    if _molparser_model is not None:
        return _molparser_model

    try:
        # Redirect library startup chatter away from stdout (JSON channel)
        with contextlib.redirect_stdout(sys.stderr):
            from molparser import MolParser

            _molparser_model = MolParser()
        return _molparser_model
    except Exception as e:
        raise RuntimeError(f"Failed to initialize MolParser model: {str(e)}")


def _smiles_to_molfile(smiles: str) -> str:
    """
    Generate a molfile with 2D coordinates from a SMILES string using RDKit.

    Args:
        smiles: SMILES string

    Returns:
        Molfile string with 2D coordinates, or an empty string if the
        SMILES cannot be parsed (e.g. extended/Markush notation).
    """
    if not smiles:
        return ""
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return ""
        AllChem.Compute2DCoords(mol)
        return Chem.MolToMolBlock(mol)
    except Exception:
        return ""


def get_molparser_prediction(
    path: Union[str, Path],
    data_type: Literal["smiles", "molfile", "both"] = "smiles",
) -> Union[Tuple[str, str], Tuple[str, str, str]]:
    """
    Get predictions from the MolParser model for a given image path.

    Args:
        path: Path to the chemical structure image.
        data_type: Type of data to return. Options:
                   "smiles" - Return only SMILES string
                   "molfile" - Return only molfile (2D coordinates from SMILES)
                   "both" - Return both SMILES and molfile

    Returns:
        If data_type is "smiles" or "molfile":
            A tuple containing (image_name, result_string)
        If data_type is "both":
            A tuple containing (image_name, smiles_string, molfile_string)

    Raises:
        FileNotFoundError: If the specified path does not exist.
        ValueError: If an invalid data_type is provided.
        RuntimeError: If the MolParser prediction fails.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    image_name = path.name

    if data_type not in ("smiles", "molfile", "both"):
        raise ValueError("Invalid data_type. Choose 'smiles', 'molfile', or 'both'.")

    # Use the singleton MolParser instance to get predictions
    parser = init_molparser()
    try:
        results = parser.parse_image(str(path), rec_only=True)
    except Exception as e:
        raise RuntimeError(f"Error in MolParser prediction: {str(e)}")

    smiles = results[0].smi if results else ""

    # MolParser outputs SMILES only; derive a molfile with 2D coordinates
    get_molfile = data_type in ("molfile", "both")
    molfile = _smiles_to_molfile(smiles) if get_molfile else ""

    # Return results based on data_type
    if data_type == "smiles":
        return image_name, smiles
    elif data_type == "molfile":
        return image_name, molfile
    else:  # data_type == "both"
        return image_name, smiles, molfile



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
    engine: Literal["molecode", "molparser"] = "molecode",
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
        # MolParser processing
        if engine == "molparser":
            # Report hardware info if available
            try:
                if torch.cuda.is_available():
                    hardware_info = f"GPU: {torch.cuda.get_device_name(0)}"
                else:
                    hardware_info = "CPU (PyTorch)"
            except Exception:
                hardware_info = "Unknown"

            if output_type == "smiles":
                image_name, smiles = get_molparser_prediction(
                    file_path, data_type="smiles"
                )
                return {
                    "image_name": image_name,
                    "smiles": smiles,
                    "engine": engine,
                    "hardware": hardware_info,
                }
            elif output_type == "molfile":
                image_name, molfile = get_molparser_prediction(
                    file_path, data_type="molfile"
                )
                return {
                    "image_name": image_name,
                    "molfile": molfile,
                    "engine": engine,
                    "hardware": hardware_info,
                }
            else:  # output_type == "both"
                image_name, smiles, molfile = get_molparser_prediction(
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
                f"Unsupported engine: {engine}. Choose 'molecode' or 'molparser'."
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing chemical structure: {str(e)}",
        )
