targetScope = 'resourceGroup'
param location string = 'norwayeast'
@minLength(3)
@maxLength(19)
param prefix string
param tags object = { project: 'DAWL', pilot: prefix }
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${prefix}acr'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${prefix}state'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: { supportsHttpsTrafficOnly: true, minimumTlsVersion: 'TLS1_2', allowBlobPublicAccess: false }
}
resource files 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  name: 'default'
  parent: storage
}
resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  name: 'pilot-state'
  parent: files
  properties: { shareQuota: 5, enabledProtocols: 'SMB' }
}
resource pullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-pull'
  location: location
  tags: tags
}
resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id,pullIdentity.id,'pull')
  properties: {
    principalId: pullIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions','7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
output registryHost string = registry.properties.loginServer
output storageName string = storage.name
output pullIdentityId string = pullIdentity.id
