"""The contract between QualityValidator and the code that reads its result.

`document_upload.py` logs `quality_assessment['overall_quality']` on the chunking
*success* path. That key was never produced, so every upload raised KeyError
there, fell into the surrounding except-handler, and re-chunked the whole
document with the fallback strategy — silently doubling the Gemini spend and
writing a second, differently-keyed set of chunk nodes.

These tests pin the shape of the result so that regression cannot return.
"""
import pytest

from backend.infrastructure.chunking.quality_validator import QualityValidator

CHUNKS = [
    {
        "chunk_id": "c0",
        "content": "The Provider shall indemnify the Client against third-party claims.",
        "size": 66,
    },
    {
        "chunk_id": "c1",
        "content": "Liability under this Agreement is capped at the fees paid in 12 months.",
        "size": 70,
    },
]
CONTEXT = {"document_type": "contract", "is_legal_document": True}


async def test_result_carries_the_keys_its_consumers_read():
    result = await QualityValidator().validate_chunks(CHUNKS, CONTEXT)

    # Read by backend/api/document_upload.py on the success path.
    assert "overall_quality" in result, (
        "document_upload.py logs quality_assessment['overall_quality']; "
        "dropping this key sends every upload down the re-chunk fallback"
    )
    for key in ("total_chunks", "passed", "failed", "chunk_scores"):
        assert key in result


async def test_overall_quality_is_the_mean_of_the_chunk_scores():
    result = await QualityValidator().validate_chunks(CHUNKS, CONTEXT)

    per_chunk = [c["scores"]["overall"] for c in result["chunk_scores"]]
    assert result["overall_quality"] == pytest.approx(sum(per_chunk) / len(per_chunk))
    assert 0.0 <= result["overall_quality"] <= 1.0


async def test_empty_input_does_not_divide_by_zero():
    result = await QualityValidator().validate_chunks([], CONTEXT)

    assert result["overall_quality"] == 0.0
    assert result["total_chunks"] == 0


async def test_the_success_path_log_line_formats():
    """Reproduces the exact expression that used to raise."""
    result = await QualityValidator().validate_chunks(CHUNKS, CONTEXT)
    chunking_result = {"quality_assessment": result, "chunk_count": len(CHUNKS)}

    rendered = (
        f"chunks: {chunking_result['chunk_count']}, "
        f"quality: {chunking_result['quality_assessment']['overall_quality']:.2f}"
    )
    assert "quality: " in rendered
