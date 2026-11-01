import importlib.metadata
import json
import time

import release


def tokenizer_fingerprint(tokenizer):
    return release.fingerprint(
        {
            "backend": json.loads(tokenizer.backend_tokenizer.to_str()),
            "special_tokens": {
                k: str(v) for k, v in tokenizer.special_tokens_map.items()
            },
            "padding_side": tokenizer.padding_side,
            "truncation_side": tokenizer.truncation_side,
        }
    )


class StubBackend:
    def __init__(self, manifest, artifact=None):
        self.loaded_id = manifest["model_id"]
        self.runtime_sha256 = manifest["runtime_sha256"]
        self.calls = 0

    def warmup(self):
        pass

    def validate_prompt(self, prompt):
        pass

    def generate(self, prompt, limit):
        self.calls += 1
        time.sleep(0.01)
        return {"answer": f"STUB ONLY: {self.loaded_id}", "output_tokens": 1}


class TransformersBackend:
    def __init__(self, manifest, artifact):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.calls = 0
        self.loaded_id = manifest["model_id"]
        self.runtime_sha256 = manifest["runtime_sha256"]
        self.runtime = manifest["runtime"]
        for name, version in self.runtime["packages"].items():
            if importlib.metadata.version(name) != version:
                raise ValueError(f"Runtime package differs from evaluation: {name}")
        self.device = self.runtime["device"]
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(artifact, local_files_only=True)
        if tokenizer_fingerprint(self.tokenizer) != self.runtime["tokenizer_sha256"]:
            raise ValueError("Loaded tokenizer differs from evaluation")
        base = (
            self.runtime["base_name"] if manifest.get("kind") == "adapter" else artifact
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            base,
            revision=self.runtime["base_revision"],
            local_files_only=True,
            dtype=getattr(torch, self.runtime["dtype"]),
            attn_implementation=self.runtime["attention_implementation"],
        )
        if manifest.get("kind") == "adapter":
            from peft import PeftModel

            self.model = PeftModel.from_pretrained(
                self.model, artifact, local_files_only=True
            )
        self.model = self.model.to(self.device).eval()
        if self.tokenizer.chat_template != self.runtime["chat_template"]:
            raise ValueError("Loaded tokenizer has a different chat template")

    def warmup(self):
        self._generate("Customer support request: billing question.", 1)

    def generate(self, prompt, limit):
        self.validate_prompt(prompt)
        self.calls += 1
        return self._generate(prompt, limit)

    def validate_prompt(self, prompt):
        text = self._prompt_text(prompt)
        if len(self.tokenizer(text)["input_ids"]) > self.runtime["max_input_tokens"]:
            raise ValueError("Input exceeds token limit")

    def _prompt_text(self, prompt):
        generation = self.runtime["generation"]
        return self.tokenizer.apply_chat_template(
            [
                {"role": "system", "content": generation["system"]},
                {"role": "user", "content": prompt},
            ],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=generation["enable_thinking"],
        )

    def _generate(self, prompt, limit):
        text = self._prompt_text(prompt)
        batch = self.tokenizer(text, return_tensors="pt").to(self.device)
        if batch["input_ids"].shape[1] > self.runtime["max_input_tokens"]:
            raise ValueError("Input exceeds token limit")
        with self.torch.inference_mode():
            out = self.model.generate(
                **batch,
                max_new_tokens=limit,
                do_sample=False,
                pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
            )
        if self.device == "mps":
            self.torch.mps.synchronize()
        tokens = out[0, batch["input_ids"].shape[1] :]
        return {
            "answer": self.tokenizer.decode(tokens, skip_special_tokens=True),
            "output_tokens": len(tokens),
        }
