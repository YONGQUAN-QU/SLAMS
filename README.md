# SLAMS: Score-based Latent Assimilation in Multimodal Setting

We recast data assimilation in multimodal setting using deep generative framework. In particular, we implement __latent score-based diffusion model__, extending previous work by Rozet, F., & Louppe, G. [[code](https://github.com/francois-rozet/sda), [paper](https://arxiv.org/abs/2306.10574)], where we further project the heterogeneous states and observations into a __unified latent space__ where the forward and reverse conditional diffusion processes take place. Through varying ablation study, given coarse, noisy, and sparse conditioning inputs, we find our method to be robust and physically consistent.

Paper: https://arxiv.org/abs/2404.06665


## Quickstart
1. Install dependencies using `pip` or `conda`
```
pip install -r requirements.txt
```

2. Run sample notebooks under `notebooks/` marked with `01_` prefix. These examples are extended from [1] to benchmark against our latent approach.
- `a`: Lorenz'63 system
- `b`: Kolmogorov fluid

## Full Experiments
In order to reproduce the results in the paper, we have to acquire the necessary data:
1. Process the in-situ data `python process_cpc.py`
2. Process the ex-situ data `python process_noaa.py`
3. Process the ERA5 data from https://leap-stc.github.io/ChaosBench/quickstart.html, particularly
```
cd data/
wget https://huggingface.co/datasets/LEAP/ChaosBench/resolve/main/process.sh
chmod +x process.sh

./process.sh era5
./process.sh climatology
```

4. Update `slams/config.py` field: `ERA_DATADIR = <YOUR_ERA5_DIR>`, for instance `<PROJECT_DIR>/SLAMS/data`

5. All evaluations are summarized in a series of `notebooks/` marked with `02_` prefix.
    - `a`: Pixel-based data assimilation
    - `b`: Latent-based data assimilation NO observation (only background states)
    - `c`: Latent-based data assimilation with +1 observation (in-situ)
    - `d`: Latent-based data assimilation with +2 observation (in-situ + ex-situ)
    - `e`: Figures and tables generation


__NOTE:__ Training your own model is simple and is defined in `train_da.py`. First, define your latent model in `slams/nn.py` or score network in `slams/score.py`. Afterwards, unify both under `slams/model_da.py`. An example, as defined in the paper, has been provided for your reference.

## Citation
If you find any of the code useful, feel free to cite these works.
```
@article{rozet2024score,
  title={Score-based data assimilation},
  author={Rozet, Fran{\c{c}}ois and Louppe, Gilles},
  journal={Advances in Neural Information Processing Systems},
  volume={36},
  year={2024}
}

@misc{qu2024deep,
      title={Deep Generative Data Assimilation in Multimodal Setting}, 
      author={Yongquan Qu and Juan Nathaniel and Shuolin Li and Pierre Gentine},
      year={2024},
      eprint={2404.06665},
      archivePrefix={arXiv},
      primaryClass={cs.CV}
}
```
