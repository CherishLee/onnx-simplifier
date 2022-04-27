import io
from typing import Any, Dict, Optional
import os

import torch
import onnx
import onnxsim
import torchvision as tv
import pytest


def export_simplify_and_check_by_python_api(
    m: torch.nn.Module,
    input: Any,
    *,
    export_kwargs: Optional[Dict[str, Any]] = None,
    simplify_kwargs: Optional[Dict[str, Any]] = None) -> onnx.ModelProto:
    if export_kwargs is None:
        export_kwargs = {}
    if simplify_kwargs is None:
        simplify_kwargs = {}
    with io.BytesIO() as f:
        torch.onnx.export(m, input, f, **export_kwargs)
        model = onnx.load_model_from_string(f.getvalue())
        sim_model, check_ok = onnxsim.simplify(model, check_n=3, **simplify_kwargs)
        assert check_ok
        return sim_model


def str_is_logical_positive(x: str) -> bool:
    return x.lower() in ["1", "on", "true"]


def skip_in_ci():
    return pytest.mark.skipif(
        str_is_logical_positive(os.getenv("ONNXSIM_CI", "")), reason="memory limited"
    )


def test_just_reshape():
    class JustReshape(torch.nn.Module):
        def __init__(self):
            super(JustReshape, self).__init__()

        def forward(self, x):
            return x.view((x.shape[0], x.shape[1], x.shape[3] * x.shape[2]))

    net = JustReshape()
    dummy_input = torch.randn(2, 3, 4, 5)
    sim_model = export_simplify_and_check_by_python_api(
        net, dummy_input, export_kwargs={"do_constant_folding": False}
    )
    assert len(sim_model.graph.node) == 1


def test_a_model_not_need_simplification():
    class ModelNotNeedSimplification(torch.nn.Module):
        def __init__(self):
            super(ModelNotNeedSimplification, self).__init__()

        def forward(self, x):
            return x + 1

    net = ModelNotNeedSimplification()
    dummy_input = torch.randn(2, 3, 4, 5)
    sim_model = export_simplify_and_check_by_python_api(net, dummy_input)
    assert len(sim_model.graph.node) == 1


def test_exprimental_simplify_subgraph():
    class WithSubGraph(torch.nn.Module):
        def __init__(self):
            super(WithSubGraph, self).__init__()

        def forward(self, x):
            if x.sum() > 1.0:
                # NOTE: even onnxsim cannot simplify it,
                # a canonical pass in onnx-optimizer is needed for it.
                # so this test only tests that include_subgraph doesn't
                # result in invalid model in this case
                return 3 + x + 3
            else:
                return x + 4

    net = torch.jit.script(WithSubGraph())
    dummy_input = torch.randn(2)
    sim_model = export_simplify_and_check_by_python_api(
        net, dummy_input,
        simplify_kwargs={"include_subgraph": True}
    )
    assert len(sim_model.graph.node) == 3
    assert len(sim_model.graph.node[2].attribute[0].g.node) == 2
    assert len(sim_model.graph.node[2].attribute[1].g.node) == 1


def test_dynamic_batch_size():
    class SimpleModel(torch.nn.Module):
        def __init__(self):
            super(SimpleModel, self).__init__()

        def forward(self, x):
            return x + 2

    net = SimpleModel()
    dummy_input = torch.randn(2, 3, 4, 5)
    sim_model = export_simplify_and_check_by_python_api(
        net,
        dummy_input,
        export_kwargs={
            "input_names": ["input"],
            "dynamic_axes": {"input": {0: "batch_size"}},
        },
        simplify_kwargs={"dynamic_input_shape": True},
    )
    assert len(sim_model.graph.node) == 1    


# NOTE: `include_subgraph` makes this test fail
@skip_in_ci()
def test_torchvision_fasterrcnn_fpn():
    model = tv.models.detection.fasterrcnn_resnet50_fpn(pretrained=False)
    x = [torch.rand(3, 300, 400), torch.rand(3, 500, 400)]
    export_simplify_and_check_by_python_api(
        model, x, export_kwargs={"opset_version": 11}
    )


# maskrcnn is only supported in opset 11 and higher
def test_torchvision_maskrcnn_fpn_opset11():
    model = tv.models.detection.maskrcnn_resnet50_fpn(pretrained=False)
    x = [torch.rand(3, 300, 400), torch.rand(3, 500, 400)]
    export_simplify_and_check_by_python_api(
        model, x, export_kwargs={"opset_version": 11}
    )


# keypointrcnn is only supported in opset 11 and higher
@skip_in_ci()
def test_torchvision_keypointrcnn_fpn():
    model = tv.models.detection.keypointrcnn_resnet50_fpn(pretrained=False)
    x = [torch.rand(3, 300, 400), torch.rand(3, 500, 400)]
    export_simplify_and_check_by_python_api(
        model, x, export_kwargs={"opset_version": 11}
    )


def test_torchvision_shufflenet_v2():
    model = tv.models.shufflenet_v2_x1_0(pretrained=False)
    x = torch.rand(1, 3, 224, 224)
    export_simplify_and_check_by_python_api(model, x)


def test_torchvision_mnasnet():
    model = tv.models.mnasnet1_0(pretrained=False)
    x = torch.rand(1, 3, 224, 224)
    export_simplify_and_check_by_python_api(model, x)


@skip_in_ci()
def test_torchvision_deeplabv3():
    model = tv.models.segmentation.deeplabv3_resnet50(pretrained=False)
    x = torch.rand(1, 3, 224, 224)
    export_simplify_and_check_by_python_api(model, x)


def test_unused_output():
    class SimpleModel(torch.nn.Module):
        def __init__(self):
            super(SimpleModel, self).__init__()

        def forward(self, x):
            x1 = x + 2
            x1 = x1 - 2
            x1 = x1 * 2
            x1 = x1 / 2
            y1 = x1
            x2 = x + 2
            x2 = x2 - 2
            x2 = x2 * 2
            x2 = x2 / 2
            y2 = x2
            x3 = x + 2
            x3 = x3 - 2
            x3 = x3 * 2
            x3 = x3 / 2
            y3 = x3
            return y1, y2, y3

    net = SimpleModel()
    dummy_input = torch.randn(2, 3, 4, 5)
    sim_model = export_simplify_and_check_by_python_api(
        net,
        dummy_input,
        export_kwargs={
            "input_names": ["input"],
            "output_names": ["output0", "output1", "output2"],
        },
        simplify_kwargs={"unused_output": ["output1", "output2"]}
    )
    assert len(sim_model.graph.node) == 4

