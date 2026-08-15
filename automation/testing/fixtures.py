from automation.models import CandidateEvidence, FieldCandidate, LocatorRecipe
def candidate(name,score_weights,**metadata):
    return FieldCandidate(LocatorRecipe("label",name),metadata.pop("tag","input"),accessible_name=name,evidence=tuple(CandidateEvidence(k,k,w) for k,w in score_weights),metadata=metadata)
