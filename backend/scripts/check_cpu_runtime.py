"""Focused, offline CPU compatibility check. No PDFs, model downloads or API calls."""
from importlib.metadata import version
import torch
from transformers import AutoModelForObjectDetection, DetrConfig, ResNetConfig
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions

for package in ('torch', 'torchvision', 'transformers', 'docling'):
    print(f'{package}: {version(package)}')
print(f'Accelerator: {torch.accelerator.current_accelerator()}')
# A tiny random detection model exercises the real Transformers/PyTorch CPU path
# without fetching pretrained weights. It does not measure extraction quality.
config = DetrConfig(
    backbone_config=ResNetConfig(depths=[1, 1, 1, 1], hidden_sizes=[8, 16, 32, 64],
                                 embedding_size=8, out_features=['stage4']),
    d_model=32, encoder_layers=1, decoder_layers=1,
    encoder_attention_heads=4, decoder_attention_heads=4,
    encoder_ffn_dim=64, decoder_ffn_dim=64, num_queries=2, num_labels=2,
)
torch.set_num_threads(2)
model = AutoModelForObjectDetection.from_config(config).to('cpu').eval()
with torch.inference_mode():
    result = model(pixel_values=torch.zeros(1, 3, 64, 64, device='cpu'))
assert result.logits.device.type == 'cpu'
assert torch.isfinite(result.logits).all()
options = PdfPipelineOptions(accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU))
converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
assert converter.format_to_options[InputFormat.PDF].pipeline_options.accelerator_options.device == 'cpu'
print('PASS: detection model CPU inference and Docling CPU configuration')
