"""
inspect_osnet.py - Print OsNet architecture and layer details
"""

import torchreid
import torch

def inspect_osnet(model_name="osnet_ain_x1_0"):
    print(f"{'='*70}")
    print(f"OsNet Architecture: {model_name}")
    print(f"{'='*70}\n")

    # Build model
    model = torchreid.models.build_model(
        name=model_name,
        num_classes=1,
        pretrained=True
    )

    # Basic info
    print(f"Feature dimension: {model.feature_dim}")
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Total parameters: {total_params:,}")
    print(f"Trainable parameters: {trainable_params:,}")

    # Top-level modules
    print(f"\n{'='*70}")
    print("TOP-LEVEL MODULES")
    print(f"{'='*70}")
    for name, module in model.named_children():
        num_params = sum(p.numel() for p in module.parameters())
        print(f"  {name:20s} | {type(module).__name__:25s} | {num_params:,} params")

    # Detailed structure
    print(f"\n{'='*70}")
    print("DETAILED LAYER STRUCTURE")
    print(f"{'='*70}")
    for name, module in model.named_children():
        print(f"\n[{name}] - {type(module).__name__}")
        # Print sub-modules if any
        sub_modules = list(module.named_children())
        if sub_modules:
            for sub_name, sub_module in sub_modules:
                sub_params = sum(p.numel() for p in sub_module.parameters())
                print(f"    -> {sub_name}: {type(sub_module).__name__} ({sub_params:,} params)")

    # Forward pass shape analysis
    print(f"\n{'='*70}")
    print("FORWARD PASS SHAPE ANALYSIS (input: 1x3x256x128)")
    print(f"{'='*70}")

    model.eval()
    x = torch.randn(1, 3, 256, 128)

    with torch.no_grad():
        print(f"  Input:        {list(x.shape)}")

        x = model.conv1(x)
        print(f"  After conv1:  {list(x.shape)}")

        x = model.maxpool(x)
        print(f"  After maxpool:{list(x.shape)}")

        x = model.conv2(x)
        print(f"  After conv2:  {list(x.shape)}")

        x = model.pool2(x)
        print(f"  After pool2:  {list(x.shape)}")

        x = model.conv3(x)
        print(f"  After conv3:  {list(x.shape)}")

        x = model.pool3(x)
        print(f"  After pool3:  {list(x.shape)}")

        x = model.conv4(x)
        print(f"  After conv4:  {list(x.shape)}")

        x = model.conv5(x)
        print(f"  After conv5:  {list(x.shape)}")

        x = model.global_avgpool(x)
        print(f"  After GAP:    {list(x.shape)}")

        x = x.view(x.size(0), -1)
        print(f"  After flatten:{list(x.shape)}")

        x = model.fc(x)
        print(f"  After fc:     {list(x.shape)}")

    # Full model print
    print(f"\n{'='*70}")
    print("FULL MODEL STRUCTURE")
    print(f"{'='*70}")
    print(model)


if __name__ == "__main__":
    inspect_osnet("osnet_ain_x1_0")
