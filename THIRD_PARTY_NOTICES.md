# Third-party notices

## Qiskit H2 Hamiltonian coefficients (Apache License 2.0)

`src/mqoes/intersection.py` (`H2_HAMILTONIAN`) contains the five Pauli coefficients of the
2-qubit H2 Hamiltonian at 0.735 Å, copied verbatim from the Qiskit tutorials. The tutorials
describe the operator as "originally computed by Qiskit Nature for an H2 molecule":

| term | coefficient |
|---|---|
| II | -1.052373245772859 |
| IZ | 0.39793742484318045 |
| ZI | -0.39793742484318045 |
| ZZ | -0.01128010425623538 |
| XX | 0.18093119978423156 |

- Source: Qiskit Algorithms tutorial *Advanced VQE Options*,
  https://qiskit-community.github.io/qiskit-algorithms/tutorials/02_vqe_advanced_options.html
  (the same operator appears in qiskit-tutorials `tutorials/algorithms/01_algorithms_introduction.ipynb`).
- Project: Qiskit Algorithms, https://github.com/qiskit-community/qiskit-algorithms,
  (C) Copyright IBM, licensed under the Apache License, Version 2.0:
  https://www.apache.org/licenses/LICENSE-2.0

No Qiskit code is included or required; this project uses only the Python standard library.
The coefficients are reproduced with attribution. Apache-2.0 material may be combined into
an AGPL-3.0 work.
