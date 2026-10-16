from langchain_core.documents import Document

def extract(path, metadata):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling_core.types.doc import TableItem
    options = PdfPipelineOptions(
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU))
    converter = DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    doc = converter.convert(str(path)).document
    result = []
    for item, _ in doc.iterate_items():
        kind = 'table' if isinstance(item, TableItem) else 'text'
        text = item.export_to_markdown(doc=doc) if kind == 'table' else getattr(item, 'text', '')
        if not isinstance(text, str) or not text.strip():
            continue
        meta = dict(metadata, type=kind)
        pages = sorted({p.page_no for p in getattr(item, 'prov', []) if isinstance(p.page_no, int) and p.page_no > 0})
        if pages:
            meta['pages'] = pages
        result.append(Document(page_content=text, metadata=meta))
    return result
