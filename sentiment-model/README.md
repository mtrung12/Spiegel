---
model-index:
- name: poltextlab/xlm-roberta-large-pooled-sentiment-v2
  results:
  - task:
      type: text-classification
    metrics:
    - name: Accuracy
      type: accuracy
      value: 91%
    - name: F1-Score
      type: f1
      value: 91%
tags:
- sentiment-analysis
- text-classification
- multilingual
- pytorch
metrics:
- precision
- recall
- f1-score
language:
- en
- hu
- fr
- cs
- sk
- pl
- de
base_model:
- xlm-roberta-large
pipeline_tag: text-classification
library_name: transformers
license: cc-by-4.0
extra_gated_prompt: Our models are intended for academic projects and academic research
  only. If you are not affiliated with an academic institution, please reach out to
  us at huggingface [at] poltextlab [dot] com for further inquiry. If we cannot clearly
  determine your academic affiliation and use case based on your form data, your request
  may be rejected. Please allow us a few business days to manually review subscriptions.
extra_gated_fields:
  Country: country
  Institution: text
  Institution Email: text
  Full Name: text
  Please specify your academic project/use case you want to use the models for: text
---

# xlm-roberta-large-pooled-sentiment-v2

An `xlm-roberta-large` model fine-tuned on sentence-level multilingual training data annotated for **sentiment classification**.

## Codebook

The model uses three sentiment labels (id2label):

* 0: Negative
* 1: Neutral
* 2: Positive

It covers 7 languages (English, German, French, Polish, Slovak, Czech and Hungarian) with nearly identical shares.

## How to use the model

```python
from transformers import AutoTokenizer, pipeline

tokenizer = AutoTokenizer.from_pretrained("xlm-roberta-large")
pipe = pipeline(
    model="poltextlab/xlm-roberta-large-pooled-sentiment-v2",
    task="text-classification",
    tokenizer=tokenizer,
    use_fast=False,
    token="<your_hf_read_only_token>"
)

text = "The food was delicious but the service was slow."
pipe(text)
```

### Gated access

Due to the gated access, you must pass the `token` parameter when loading the model. In earlier versions of the Transformers package, you may need to use the `use_auth_token` parameter instead.

# Classification Report

## Overall Performance:

* **Accuracy:** 91%
* **Macro Avg:** Precision: 0.91, Recall: 0.91, F1-score: 0.91
* **Weighted Avg:** Precision: 0.91, Recall: 0.91, F1-score: 0.91

## Per-Class Metrics:

| Label    | Precision | Recall | F1-score | Support |
| -------- | --------- | ------ | -------- | ------- |
| Negative | 0.94      | 0.92   | 0.93     | 13048   |
| Neutral  | 0.88      | 0.85   | 0.87     | 9356    |
| Positive | 0.89      | 0.95   | 0.92     | 9373    |

## Per-Language Performance

Detailed classification results for each language are available below:

* Slovak (sk)
* Czech (cs)
* French (fr)
* Hungarian (hu)
* English (en)
* Polish (pl)
* German (de)

(See confusion matrices and tables in the repository for each language.)

![Language PRF Line Plot](language_prf_lineplot.png)


#### Slovak (sk)
| label | precision | recall | f1-score | support |
|-------|-----------|--------|----------|---------|
| 0     | 0.938 | 0.935 | 0.936 | 1865 |
| 1     | 0.897 | 0.849 | 0.872 | 1338 |
| 2     | 0.898 | 0.948 | 0.922 | 1339 |
| **accuracy** |   |   | **0.914** | 4542 |
| **macro avg** | 0.911 | 0.911 | 0.910 | 4542 |
| **weighted avg** | 0.914 | 0.914 | 0.913 | 4542 |

#### Czech (cz)
| label | precision | recall | f1-score | support |
|-------|-----------|--------|----------|---------|
| 0     | 0.949 | 0.923 | 0.936 | 1866 |
| 1     | 0.887 | 0.877 | 0.882 | 1340 |
| 2     | 0.905 | 0.949 | 0.926 | 1340 |
| **accuracy** |   |   | **0.917** | 4546 |
| **macro avg** | 0.914 | 0.916 | 0.915 | 4546 |
| **weighted avg** | 0.918 | 0.917 | 0.917 | 4546 |

#### French (fr)
| label | precision | recall | f1-score | support |
|-------|-----------|--------|----------|---------|
| 0     | 0.948 | 0.913 | 0.930 | 1854 |
| 1     | 0.868 | 0.864 | 0.866 | 1319 |
| 2     | 0.890 | 0.940 | 0.914 | 1334 |
| **accuracy** |   |   | **0.907** | 4507 |
| **macro avg** | 0.902 | 0.906 | 0.903 | 4507 |
| **weighted avg** | 0.907 | 0.907 | 0.907 | 4507 |

#### Hungarian (hu)
| label | precision | recall | f1-score | support |
|-------|-----------|--------|----------|---------|
| 0     | 0.944 | 0.917 | 0.930 | 1866 |
| 1     | 0.875 | 0.856 | 0.865 | 1340 |
| 2     | 0.897 | 0.952 | 0.924 | 1341 |
| **accuracy** |   |   | **0.909** | 4547 |
| **macro avg** | 0.905 | 0.908 | 0.907 | 4547 |
| **weighted avg** | 0.910 | 0.909 | 0.909 | 4547 |

#### English (en)
| label | precision | recall | f1-score | support |
|-------|-----------|--------|----------|---------|
| 0     | 0.935 | 0.932 | 0.934 | 1866 |
| 1     | 0.900 | 0.836 | 0.867 | 1339 |
| 2     | 0.888 | 0.954 | 0.920 | 1339 |
| **accuracy** |   |   | **0.910** | 4544 |
| **macro avg** | 0.908 | 0.908 | 0.907 | 4544 |
| **weighted avg** | 0.911 | 0.910 | 0.910 | 4544 |

#### Polish (pl)
| label | precision | recall | f1-score | support |
|-------|-----------|--------|----------|---------|
| 0     | 0.939 | 0.923 | 0.931 | 1866 |
| 1     | 0.885 | 0.841 | 0.863 | 1340 |
| 2     | 0.885 | 0.951 | 0.917 | 1341 |
| **accuracy** |   |   | **0.907** | 4547 |
| **macro avg** | 0.903 | 0.905 | 0.903 | 4547 |
| **weighted avg** | 0.907 | 0.907 | 0.907 | 4547 |

#### German (ger)
| label | precision | recall | f1-score | support |
|-------|-----------|--------|----------|---------|
| 0     | 0.931 | 0.923 | 0.927 | 1865 |
| 1     | 0.869 | 0.844 | 0.856 | 1340 |
| 2     | 0.895 | 0.932 | 0.913 | 1339 |
| **accuracy** |   |   | **0.903** | 4544 |
| **macro avg** | 0.899 | 0.900 | 0.899 | 4544 |
| **weighted avg** | 0.902 | 0.903 | 0.902 | 4544 |

## Inference platform

This model is used by the [Babel Machine](https://babel.poltextlab.com), an open-source and free natural language processing tool, designed to simplify and speed up projects for comparative research.

## Cooperation

Model performance can be significantly improved by extending our training sets. We appreciate every submission of coded corpora (of any domain and language) at poltextlab{at}poltextlab{dot}com or by using the [Babel Machine](https://babel.poltextlab.com).

## Debugging and issues

This architecture uses the `sentencepiece` tokenizer. In order to use the model before `transformers==4.27` you need to install it manually.

If you encounter a `RuntimeError` when loading the model using the `from_pretrained()` method, adding `ignore_mismatched_sizes=True` should solve the issue.
