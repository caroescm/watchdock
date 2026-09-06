import os
from openai import OpenAI

MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"


def get_client():
    return OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=os.environ["NVIDIA_API_KEY"],
    )