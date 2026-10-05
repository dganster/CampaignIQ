"""Prepare and validate the inputs for one CampaignIQ monthly import."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Mapping

from campaigniq.domain.lot_book import LotBook
from campaigniq.import_contract import (
    MonthlyImportContract,
    MonthlyInputRequirement,
    MonthlyInputRole,
    monthly_import_contract,
)
from campaigniq.import_validation import (
    MonthlyInputValidation,
    validate_monthly_input,
)
from campaigniq.importers.schwab.position_snapshot import to_lots
from campaigniq.importers.schwab.position_snapshot_reader import (
    read_position_snapshot_section,
)
from campaigniq.persistence.artifact_storage import ArtifactStorage
from campaigniq.persistence.authoritative_lot_state import (
    AuthoritativeOpeningState,
    load_preceding_authoritative_state,
    load_preceding_authoritative_state_from_storage,
    lot_state_path,
)


@dataclass(frozen=True, slots=True)
class MonthlyImportPreflight:
    """One UI-ready preflight result for a requested calendar month."""

    contract: MonthlyImportContract
    validations: tuple[MonthlyInputValidation, ...]
    opening_state: AuthoritativeOpeningState | None
    bootstrap_opening_lot_book: LotBook | None = None
    forex_applicable: bool = True
    crypto_applicable: bool = True

    @property
    def ready(self) -> bool:
        """Whether every required input is available and valid."""
        required_roles = {
            requirement.role
            for requirement in self.contract.requirements
            if requirement.required
        }
        valid_roles = {
            validation.role for validation in self.validations if validation.valid
        }
        return required_roles <= valid_roles

    @property
    def opening_lot_book(self) -> LotBook | None:
        """Return the authoritative or validated bootstrap opening lot book."""
        if self.opening_state is not None:
            return self.opening_state.lot_book
        return self.bootstrap_opening_lot_book

    def validation_for(
        self,
        role: MonthlyInputRole,
    ) -> MonthlyInputValidation:
        """Return the status for one semantic input role."""
        for validation in self.validations:
            if validation.role is role:
                return validation
        raise KeyError(role)


def prepare_monthly_import(
    year: int,
    month: int,
    *,
    authoritative_state_root: str | Path,
    supplied_inputs: Mapping[MonthlyInputRole, str | Path],
    artifact_storage: ArtifactStorage | None = None,
    forex_applicable: bool = True,
    crypto_applicable: bool = True,
) -> MonthlyImportPreflight:
    """Combine the monthly contract, file validation, and predecessor discovery."""
    contract = monthly_import_contract(year, month)
    if not forex_applicable:
        contract = replace(contract, requirements=tuple(
            replace(req, required=False) if req.role == MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT else req
            for req in contract.requirements))
    if artifact_storage is None:
        opening_state = load_preceding_authoritative_state(
            authoritative_state_root,
            period_start=contract.period_start,
        )
    else:
        stored = load_preceding_authoritative_state_from_storage(
            artifact_storage,
            period_start=contract.period_start,
        )
        opening_state = None
        if stored is not None:
            period_end, _key, lot_book = stored
            opening_state = AuthoritativeOpeningState(
                period_end=period_end,
                path=lot_state_path(authoritative_state_root, period_end=period_end),
                lot_book=lot_book,
            )

    validations: list[MonthlyInputValidation] = []
    bootstrap_opening_lot_book: LotBook | None = None

    if opening_state is None:
        bootstrap_path = supplied_inputs.get(
            MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT
        )
        if bootstrap_path is not None:
            bootstrap_validation = validate_monthly_input(
                MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT,
                bootstrap_path,
                period_start=contract.period_start,
                period_end=contract.period_end,
            )
            if bootstrap_validation.valid:
                snapshot_end = contract.period_start - contract.period_start.resolution
                rows = read_position_snapshot_section(
                    Path(bootstrap_path).read_text(encoding="utf-8").splitlines(),
                    snapshot_at=datetime.combine(
                        snapshot_end,
                        datetime.max.time(),
                    ),
                )
                bootstrap_opening_lot_book = LotBook()
                for lot in to_lots(list(rows)):
                    bootstrap_opening_lot_book.seed(lot)

    for requirement in contract.requirements:
        role = requirement.role

        if role is MonthlyInputRole.OPENING_STATE:
            if opening_state is not None:
                validations.append(
                    MonthlyInputValidation(
                        role=role,
                        valid=True,
                        message=(
                            "Authoritative opening lot state available for "
                            f"{opening_state.period_end}."
                        ),
                    )
                )
            elif bootstrap_opening_lot_book is not None:
                validations.append(
                    MonthlyInputValidation(
                        role=role,
                        valid=True,
                        message=(
                            "Opening lot state reconstructed from the validated "
                            "prior month-end Schwab position snapshot."
                        ),
                    )
                )
            else:
                validations.append(
                    MonthlyInputValidation(
                        role=role,
                        valid=False,
                        message=(
                            "No authoritative opening lot state is available for "
                            f"{contract.period_start - contract.period_start.resolution}. "
                            "A validated opening-state reconstruction is required. "
                            "Supply the prior month-end Schwab Brokerage Statement "
                            "to establish the opening inventory."
                        ),
                    )
                )
            continue

        if not requirement.user_supplied:
            validations.append(
                MonthlyInputValidation(
                    role=role,
                    valid=not requirement.required,
                    message=(
                        "CampaignIQ will discover this evidence if boundary "
                        "reconstruction requires it."
                    ),
                )
            )
            continue

        path = supplied_inputs.get(role)
        if role == MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT and not forex_applicable:
            validations.append(MonthlyInputValidation(role=role, valid=True, message="Not applicable: no separate Forex report requested."))
            continue

        if (
            role is MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT
            and opening_state is not None
        ):
            validations.append(
                MonthlyInputValidation(
                    role=role,
                    valid=True,
                    message=(
                        "Prior month-end statement is not required because "
                        "CampaignIQ has an authoritative preceding state."
                    ),
                )
            )
            continue

        if path is None:
            validations.append(
                MonthlyInputValidation(
                    role=role,
                    valid=not requirement.required,
                    message=(
                        "Optional input not supplied."
                        if not requirement.required
                        else "Required input has not been supplied."
                    ),
                )
            )
            continue

        validations.append(
            validate_monthly_input(
                role,
                path,
                period_start=contract.period_start,
                period_end=contract.period_end,
            )
        )

    if not forex_applicable or not crypto_applicable:
        from campaigniq.market_applicability import validate_market_applicability
        from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
        contract = replace(contract, requirements=(*contract.requirements,
            MonthlyInputRequirement(role=MonthlyInputRole.MARKET_APPLICABILITY, required=True,
                                    user_supplied=False, description="Verify markets declared not applicable.")))
        try:
            validate_market_applicability(
                supplied_inputs.get(MonthlyInputRole.THINKORSWIM_TRADE_HISTORY),
                period_start=contract.period_start, period_end=contract.period_end,
                opening_lot_book=opening_state.lot_book if opening_state else bootstrap_opening_lot_book,
                storage=artifact_storage or LocalFilesystemArtifactStorage(Path(authoritative_state_root)),
                forex_applicable=forex_applicable, crypto_applicable=crypto_applicable)
            validations.append(MonthlyInputValidation(role=MonthlyInputRole.MARKET_APPLICABILITY, valid=True,
                message="Not-applicable choices verified against retained activity and opening positions."))
        except (ValueError, OSError, KeyError) as exc:
            validations.append(MonthlyInputValidation(role=MonthlyInputRole.MARKET_APPLICABILITY, valid=False, message=str(exc)))

    statement_path = supplied_inputs.get(MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT)
    if statement_path is not None:
        contract = replace(contract, requirements=(*contract.requirements,
            MonthlyInputRequirement(role=MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT, required=True,
                                    user_supplied=True, description="Validate the supplied optional Crypto Statement.")))
        try:
            from campaigniq.importers.schwab.crypto_statement_reader import read_crypto_statement
            if not crypto_applicable:
                raise ValueError("Crypto Statement cannot be supplied when Crypto is Not applicable.")
            read_crypto_statement(statement_path, period_start=contract.period_start, period_end=contract.period_end)
            validations.append(MonthlyInputValidation(role=MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT, valid=True,
                message="Statement account, period, cash balances, holdings, and transaction layout recognized. Matching occurs in crypto review."))
        except (ValueError, OSError) as exc:
            validations.append(MonthlyInputValidation(role=MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT, valid=False, message=str(exc)))

    return MonthlyImportPreflight(
        contract=contract,
        validations=tuple(validations),
        opening_state=opening_state,
        bootstrap_opening_lot_book=bootstrap_opening_lot_book,
        forex_applicable=forex_applicable,
        crypto_applicable=crypto_applicable,
    )
