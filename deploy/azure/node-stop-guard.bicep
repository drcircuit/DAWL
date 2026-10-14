// Restore the existing pilot stop permission independently of ACI provisioning.
targetScope = 'resourceGroup'
param prefix string
resource pool 'Microsoft.ContainerInstance/containerGroups@2023-05-01' existing = {
  name: '${prefix}-nodes'
}
resource deadline 'Microsoft.Logic/workflows@2019-05-01' existing = {
  name: '${prefix}-nodes-deadline'
}
resource stopRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(pool.id,deadline.id,'stop')
  scope: pool
  properties: {
    principalId: deadline.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions','b24988ac-6180-42a0-ab88-20f7382dd24c')
  }
}
