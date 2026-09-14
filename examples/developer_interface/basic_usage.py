"""
Example: basic Platrixa usage (no configuration).

Run from the repository root:

    python3 examples/developer_interface/basic_usage.py

The public interface is a thin boundary over the Platrixa Kernel:
this script configures nothing and calls Kernel.process exactly once
per transaction, through platrixa.Platrixa.process.

NOTE: without a configured provider this uses "auto" selection — with
PLATRIXA_MODEL_ENDPOINT_URL unset it means the local Hugging Face
provider, which downloads/loads the pinned model on first use. Set
PLATRIXA_MODEL_ENDPOINT_URL to use the remote runtime instead.
"""

from platrixa import Platrixa

TRANSACTION = "Purchased furniture for cash ₹15,000"


def main() -> None:
    client = Platrixa()  # default: auto provider selection
    result = client.process(TRANSACTION)

    print(f"state:          {result.status} ({result.status_label})")
    print(f"request id:     {result.request_id}")
    if result.interpretation:
        print(f"transaction:    {result.interpretation.get('transaction_type')}")
        print(f"parties:        {result.interpretation.get('parties')}")
        print(f"payment method: {result.interpretation.get('payment_method')}")
    if result.accounting:
        print(f"debit lines:    {result.accounting.get('debit_lines')}")
        print(f"credit lines:   {result.accounting.get('credit_lines')}")
    if result.issues:
        print(f"issues:         {result.issues}")
    if result.next_action:
        print(f"next action:    {result.next_action}")


if __name__ == "__main__":
    main()
